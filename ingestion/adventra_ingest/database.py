from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
from psycopg import Connection

from .embeddings import EMBEDDING_DIMENSIONS
from .models import BookDocument, RagChunk

IMPORTER_VERSION = "1.0.0"


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
    connection: Connection, book: BookDocument, *, force: bool = False
) -> bool:
    if force:
        return False
    row = connection.execute(
        """
        SELECT 1
        FROM book_editions
        WHERE book_id = %s
          AND language = %s
          AND source_sha256 = %s
          AND is_active
        """,
        (book.book_id, book.language, book.source_sha256),
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
        INSERT INTO content_imports (
            import_id, source_path, source_sha256, importer_version,
            status, book_id, language
        )
        VALUES (%s, %s, %s, %s, 'processing', %s, %s)
        """,
        (
            import_id,
            book.source_path,
            book.source_sha256,
            IMPORTER_VERSION,
            book.book_id,
            book.language,
        ),
    )
    connection.commit()

    try:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{book.book_id}:{book.language}",),
            )
            connection.execute(
                """
                INSERT INTO books (
                    book_id, title, author, source_identifier, publisher
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (book_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    author = EXCLUDED.author,
                    source_identifier = EXCLUDED.source_identifier,
                    publisher = EXCLUDED.publisher,
                    updated_at = NOW()
                """,
                (
                    book.book_id,
                    book.title,
                    book.author,
                    book.source_identifier,
                    book.publisher,
                ),
            )
            connection.execute(
                """
                UPDATE book_editions
                SET is_active = FALSE
                WHERE book_id = %s AND language = %s
                """,
                (book.book_id, book.language),
            )
            connection.execute(
                """
                INSERT INTO book_editions (
                    edition_id, book_id, language, source_format, source_path,
                    source_sha256, content_version, import_id, is_active
                )
                VALUES (%s, %s, %s, 'epub', %s, %s, %s, %s, TRUE)
                ON CONFLICT (edition_id) DO UPDATE SET
                    source_path = EXCLUDED.source_path,
                    source_sha256 = EXCLUDED.source_sha256,
                    content_version = EXCLUDED.content_version,
                    import_id = EXCLUDED.import_id,
                    is_active = TRUE,
                    imported_at = NOW()
                """,
                (
                    edition_id,
                    book.book_id,
                    book.language,
                    book.source_path,
                    book.source_sha256,
                    book.content_version,
                    import_id,
                ),
            )
            connection.execute(
                """
                UPDATE chapters
                SET is_active = FALSE, updated_at = NOW()
                WHERE book_id = %s AND language = %s
                """,
                (book.book_id, book.language),
            )
            connection.execute(
                """
                UPDATE passages
                SET is_active = FALSE, updated_at = NOW()
                WHERE book_id = %s AND language = %s
                """,
                (book.book_id, book.language),
            )

            for chapter in book.chapters:
                connection.execute(
                    """
                    INSERT INTO chapters (
                        chapter_id, edition_id, book_id, language,
                        chapter_number, title, is_active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (chapter_id) DO UPDATE SET
                        edition_id = EXCLUDED.edition_id,
                        title = EXCLUDED.title,
                        is_active = TRUE,
                        updated_at = NOW()
                    """,
                    (
                        chapter.chapter_id,
                        edition_id,
                        book.book_id,
                        book.language,
                        chapter.number,
                        chapter.title,
                    ),
                )
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO passages (
                            passage_id, edition_id, chapter_id, book_id,
                            language, chapter_number, paragraph_number,
                            sequence, text, content_hash, is_active
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE
                        )
                        ON CONFLICT (passage_id) DO UPDATE SET
                            edition_id = EXCLUDED.edition_id,
                            chapter_id = EXCLUDED.chapter_id,
                            text = EXCLUDED.text,
                            content_hash = EXCLUDED.content_hash,
                            is_active = TRUE,
                            updated_at = NOW()
                        """,
                        [
                            (
                                passage.passage_id,
                                edition_id,
                                chapter.chapter_id,
                                book.book_id,
                                book.language,
                                chapter.number,
                                passage.paragraph_number,
                                passage.sequence,
                                passage.text,
                                passage.content_hash,
                            )
                            for passage in chapter.passages
                        ],
                    )

            connection.execute(
                "DELETE FROM rag_chunks WHERE book_id = %s AND language = %s",
                (book.book_id, book.language),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO rag_chunks (
                        chunk_id, edition_id, chapter_id, book_id, language,
                        chunk_number, passage_ids, title, text, token_count,
                        content_hash, embedding, embedding_model,
                        embedding_dimensions, content_version
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s::vector, %s, %s, %s
                    )
                    """,
                    [
                        (
                            chunk.chunk_id,
                            edition_id,
                            chunk.chapter_id,
                            book.book_id,
                            book.language,
                            chunk.chunk_number,
                            list(chunk.passage_ids),
                            chunk.title,
                            chunk.text,
                            chunk.token_count,
                            chunk.content_hash,
                            _vector_literal(chunk.embedding),
                            embedding_model,
                            EMBEDDING_DIMENSIONS,
                            book.content_version,
                        )
                        for chunk in chunks
                    ],
                )
            passage_count = sum(
                len(chapter.passages) for chapter in book.chapters
            )
            connection.execute(
                """
                UPDATE content_imports
                SET status = 'succeeded',
                    chapter_count = %s,
                    passage_count = %s,
                    chunk_count = %s,
                    completed_at = NOW()
                WHERE import_id = %s
                """,
                (len(book.chapters), passage_count, len(chunks), import_id),
            )
    except Exception as error:
        connection.rollback()
        connection.execute(
            """
            UPDATE content_imports
            SET status = 'failed',
                error_message = %s,
                completed_at = NOW()
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
