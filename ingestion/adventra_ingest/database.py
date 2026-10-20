from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
from psycopg import Connection

from .embeddings import EMBEDDING_DIMENSIONS
from .models import BookDocument, RagChunk

IMPORTER_VERSION = "2.0.0"


def connect_from_environment() -> Connection:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)

    required = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing PostgreSQL configuration: {', '.join(missing)}"
        )
    return psycopg.connect(
        host=os.environ["PGHOST"],
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        port=int(os.getenv("PGPORT", "5432")),
        sslmode=os.getenv("PGSSLMODE", "require"),
    )


def apply_schema(connection: Connection, schema_path: Path) -> None:
    schema = schema_path.read_text(encoding="utf-8")
    with connection.transaction():
        connection.execute(schema, prepare=False)


def is_imported(
    connection: Connection,
    book: BookDocument,
    *,
    embedding_model: str,
    force: bool = False,
) -> bool:
    if force:
        return False
    row = connection.execute(
        """
        SELECT 1
        FROM content_ingestion.editions e
        JOIN content_ingestion.imports i ON i.import_id = e.import_id
        JOIN public.book_editions reader
          ON reader.book_id = e.book_id AND reader.language = e.language
        WHERE e.book_id = %s AND e.language = %s AND e.source_sha256 = %s
          AND e.is_active AND reader.is_active
          AND i.status = 'succeeded' AND i.importer_version = %s
          AND e.embedding_model = %s
          AND e.canonical_sha256 = public.content_source_sha256(e.book_id, e.language)
        """,
        (book.book_id, book.language, book.source_sha256, IMPORTER_VERSION, embedding_model),
    ).fetchone()
    return row is not None


def store_book(
    connection: Connection,
    book: BookDocument,
    chunks: tuple[RagChunk, ...],
    *,
    embedding_model: str,
) -> uuid.UUID:
    _validate_embeddings(chunks)
    import_id = uuid.uuid4()
    edition_id = f"{book.book_id}:{book.language}:{book.content_version}"
    connection.execute(
        """
        INSERT INTO content_ingestion.imports (
            import_id, source_path, source_sha256, importer_version,
            status, book_id, language
        ) VALUES (%s, %s, %s, %s, 'processing', %s, %s)
        """,
        (import_id, book.source_path, book.source_sha256, IMPORTER_VERSION,
         book.book_id, book.language),
    )
    connection.commit()

    try:
        with connection.transaction():
            # Titles/authors are shared across languages, so serialize by book.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (book.book_id,),
            )
            connection.execute(
                """
                INSERT INTO public.books (book_id, title, author)
                VALUES (%s, %s, %s)
                ON CONFLICT (book_id) DO UPDATE SET
                    title = EXCLUDED.title, author = EXCLUDED.author
                """,
                (book.book_id, book.title, book.author),
            )
            connection.execute(
                """
                INSERT INTO public.book_editions (book_id, language, is_active)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (book_id, language) DO UPDATE SET is_active = TRUE
                """,
                (book.book_id, book.language),
            )
            connection.execute(
                "UPDATE public.passages SET is_active = FALSE WHERE book_id = %s AND language = %s",
                (book.book_id, book.language),
            )
            for chapter in book.chapters:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO public.passages (
                            passage_id, book_id, language, chapter_number,
                            paragraph_number, sequence, text, page_start,
                            page_end, page_paragraph, is_active
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (passage_id) DO UPDATE SET
                            chapter_number = EXCLUDED.chapter_number,
                            paragraph_number = EXCLUDED.paragraph_number,
                            sequence = EXCLUDED.sequence, text = EXCLUDED.text,
                            page_start = EXCLUDED.page_start, page_end = EXCLUDED.page_end,
                            page_paragraph = EXCLUDED.page_paragraph, is_active = TRUE
                        WHERE public.passages.book_id = EXCLUDED.book_id
                          AND public.passages.language = EXCLUDED.language
                        """,
                        [(p.passage_id, book.book_id, book.language, chapter.number,
                          p.paragraph_number, p.sequence, p.text,
                          p.page_start, p.page_end, p.page_paragraph)
                         for p in chapter.passages],
                    )
                    if cursor.rowcount != len(chapter.passages):
                        raise ValueError("Passage ID belongs to another book or language")
            canonical_hash = connection.execute(
                "SELECT public.content_source_sha256(%s, %s)",
                (book.book_id, book.language),
            ).fetchone()
            if canonical_hash is None or canonical_hash[0] is None:
                raise ValueError("Imported reader corpus has invalid or missing passage locations")
            connection.execute(
                "UPDATE content_ingestion.editions SET is_active = FALSE WHERE book_id = %s AND language = %s",
                (book.book_id, book.language),
            )
            connection.execute(
                """
                INSERT INTO content_ingestion.editions (
                    edition_id, book_id, language, source_identifier, publisher,
                    source_format, source_path, source_sha256, content_version,
                    canonical_sha256, embedding_model, import_id, is_active
                ) VALUES (%s, %s, %s, %s, %s, 'epub', %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (edition_id) DO UPDATE SET
                    source_identifier = EXCLUDED.source_identifier,
                    publisher = EXCLUDED.publisher, source_path = EXCLUDED.source_path,
                    source_sha256 = EXCLUDED.source_sha256, import_id = EXCLUDED.import_id,
                    canonical_sha256 = EXCLUDED.canonical_sha256,
                    embedding_model = EXCLUDED.embedding_model,
                    is_active = TRUE, imported_at = NOW()
                """,
                (edition_id, book.book_id, book.language, book.source_identifier,
                 book.publisher, book.source_path, book.source_sha256,
                 book.content_version, canonical_hash[0], embedding_model, import_id),
            )
            connection.execute(
                "UPDATE content_ingestion.chapters SET is_active = FALSE WHERE book_id = %s AND language = %s",
                (book.book_id, book.language),
            )
            for chapter in book.chapters:
                chapter_result = connection.execute(
                    """
                    INSERT INTO content_ingestion.chapters (
                        chapter_id, edition_id, book_id, language, chapter_number, title, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (chapter_id) DO UPDATE SET
                        edition_id = EXCLUDED.edition_id, title = EXCLUDED.title,
                        chapter_number = EXCLUDED.chapter_number, is_active = TRUE
                    WHERE content_ingestion.chapters.book_id = EXCLUDED.book_id
                      AND content_ingestion.chapters.language = EXCLUDED.language
                    """,
                    (chapter.chapter_id, edition_id, book.book_id, book.language,
                     chapter.number, chapter.title),
                )
                if chapter_result.rowcount != 1:
                    raise ValueError("Chapter ID belongs to another book or language")
            connection.execute(
                "DELETE FROM content_ingestion.rag_chunks WHERE book_id = %s AND language = %s",
                (book.book_id, book.language),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO content_ingestion.rag_chunks (
                        chunk_id, edition_id, chapter_id, book_id, language,
                        chunk_number, passage_ids, title, text, token_count,
                        content_hash, embedding, embedding_model,
                        embedding_dimensions, content_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s::vector, %s, %s, %s)
                    """,
                    [(c.chunk_id, edition_id, c.chapter_id, book.book_id, book.language,
                      c.chunk_number, list(c.passage_ids), c.title, c.text, c.token_count,
                      c.content_hash, _vector_literal(c.embedding), embedding_model,
                      EMBEDDING_DIMENSIONS, book.content_version) for c in chunks],
                )
            connection.execute(
                """
                UPDATE content_ingestion.imports
                SET status = 'succeeded', chapter_count = %s, passage_count = %s,
                    chunk_count = %s, completed_at = NOW()
                WHERE import_id = %s
                """,
                (len(book.chapters), sum(len(c.passages) for c in book.chapters),
                 len(chunks), import_id),
            )
    except Exception as error:
        connection.rollback()
        connection.execute(
            """
            UPDATE content_ingestion.imports
            SET status = 'failed', error_message = %s, completed_at = NOW()
            WHERE import_id = %s
            """,
            (str(error)[:4000], import_id),
        )
        connection.commit()
        raise
    return import_id


def _validate_embeddings(chunks: tuple[RagChunk, ...]) -> None:
    for chunk in chunks:
        if len(chunk.embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Chunk {chunk.chunk_id} has {len(chunk.embedding)} dimensions; "
                f"expected {EMBEDDING_DIMENSIONS}"
            )


def _vector_literal(values: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"
