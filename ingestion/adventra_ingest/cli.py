from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from .chunking import build_chunks
from .database import (
    apply_schema,
    connect_from_environment,
    is_imported,
    store_book,
)
from .embeddings import AzureOpenAIEmbedder
from .epub import parse_epub


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest EPUB books into Adventra PostgreSQL and pgvector"
    )
    parser.add_argument(
        "patterns",
        nargs="+",
        help="EPUB paths or glob patterns, for example books/**/*.epub",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path.cwd(),
        help="Root used to store repository-relative source paths",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).parents[1] / "sql" / "001_content_rag.sql",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk without connecting to PostgreSQL or Azure OpenAI",
    )
    args = parser.parse_args(argv)

    paths = _expand_paths(args.patterns)
    if not paths:
        parser.error("No EPUB files matched the supplied paths")

    books = [parse_epub(path, args.source_root) for path in paths]
    chunk_sets = [build_chunks(book) for book in books]
    if args.dry_run:
        print(
            json.dumps(
                [
                    {
                        "source": book.source_path,
                        "book_id": book.book_id,
                        "title": book.title,
                        "language": book.language,
                        "chapters": len(book.chapters),
                        "passages": sum(
                            len(chapter.passages) for chapter in book.chapters
                        ),
                        "chunks": len(chunks),
                    }
                    for book, chunks in zip(books, chunk_sets, strict=True)
                ],
                indent=2,
            )
        )
        return 0

    embedder = AzureOpenAIEmbedder.from_environment()
    with connect_from_environment() as connection:
        apply_schema(connection, args.schema)
        for book, chunks in zip(books, chunk_sets, strict=True):
            if is_imported(connection, book, force=args.force):
                print(
                    f"Skipping unchanged book {book.book_id} "
                    f"({book.content_version})"
                )
                continue
            print(
                f"Embedding {len(chunks)} chunks for "
                f"{book.title} [{book.language}]"
            )
            embedded = embedder.embed(chunks)
            import_id = store_book(
                connection,
                book,
                embedded,
                embedding_model=embedder.model_name,
            )
            print(
                f"Imported {book.title}: {len(book.chapters)} chapters, "
                f"{sum(len(ch.passages) for ch in book.chapters)} passages, "
                f"{len(embedded)} chunks (import {import_id})"
            )
    return 0


def _expand_paths(patterns: list[str]) -> list[Path]:
    matches: set[Path] = set()
    for pattern in patterns:
        path = Path(pattern)
        if path.is_file():
            matches.add(path)
            continue
        matches.update(
            candidate
            for value in glob.glob(pattern, recursive=True)
            if (candidate := Path(value)).is_file()
        )
    return sorted(matches)


if __name__ == "__main__":
    sys.exit(main())

