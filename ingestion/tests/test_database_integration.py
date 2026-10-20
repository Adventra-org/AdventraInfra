"""Real Backend migration/ingestion contract; uses disposable databases only."""

from dataclasses import replace
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
import pytest

from adventra_ingest.database import apply_schema, is_imported, store_book
from adventra_ingest.embeddings import EMBEDDING_DIMENSIONS
from adventra_ingest.models import BookDocument, Chapter, Passage, RagChunk
from adventra_ingest.chunking import build_chunks
from adventra_ingest.epub import parse_epub

SCHEMA = Path(__file__).parents[1] / "sql" / "001_content_rag.sql"
MODEL = "text-embedding-3-small"


@pytest.fixture
def database():
    url = os.getenv("INGESTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set INGESTION_TEST_DATABASE_URL to run PostgreSQL/pgvector contracts")
    name = f"ingestion_contract_{uuid.uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with psycopg.connect(url, dbname=name) as connection:
                yield connection
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def migrate(connection):
    path = Path(os.environ.get(
        "BACKEND_MIGRATIONS_PATH",
        str(Path(__file__).resolve().parents[3] / "AdventraBackend" / "migrations"),
    ))
    migrations = sorted(path.glob("*.sql"))
    assert migrations, f"Backend migrations not found at {path}"
    for migration in migrations:
        with connection.transaction():
            connection.execute(migration.read_text(), prepare=False)


def document(version="v1", language="en", text="Synthetic paragraph."):
    chapter_id = f"synthetic:{language}:chapter-001"
    passage = Passage(
        passage_id=f"{chapter_id}:p0001", chapter_id=chapter_id,
        chapter_number=1, paragraph_number=1, sequence=1, text=text,
        content_hash=version, page_start=10, page_end=10, page_paragraph=1,
    )
    return BookDocument(
        book_id="synthetic", title="Synthetic work", author="Test author",
        language=language, source_identifier="test-source", publisher="Test publisher",
        source_path="synthetic.epub", source_sha256=version, content_version=version,
        chapters=(Chapter(chapter_id=chapter_id, number=1, title="Test chapter",
                          passages=(passage,)),),
    )


def chunks(book):
    chapter = book.chapters[0]
    passage = chapter.passages[0]
    return (RagChunk(
        chunk_id=f"{chapter.chapter_id}:c0001", chapter_id=chapter.chapter_id,
        chapter_number=1, chunk_number=1, passage_ids=(passage.passage_id,),
        title=chapter.title, text=passage.text, token_count=3,
        content_hash=book.content_version,
        embedding=(0.01,) * EMBEDDING_DIMENSIONS,
    ),)


def store(connection, book):
    return store_book(connection, book, chunks(book), embedding_model=MODEL)


def imported(connection, book, **kwargs):
    return is_imported(connection, book, embedding_model=MODEL, **kwargs)


def test_requires_backend_migrations_without_creating_reader_tables(database):
    with pytest.raises(psycopg.errors.RaiseException, match="Apply all AdventraBackend"):
        apply_schema(database, SCHEMA)
    assert database.execute("SELECT to_regclass('public.books')").fetchone() == (None,)


def test_backend_first_initial_import_and_idempotence(database):
    migrate(database)
    before = database.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='public.book_editions'::regclass ORDER BY conname"
    ).fetchall()
    database.commit()
    apply_schema(database, SCHEMA)
    apply_schema(database, SCHEMA)
    book = document()
    assert not imported(database, book)
    store(database, book)
    assert imported(database, book)
    assert not imported(database, book, force=True)
    assert not is_imported(database, book, embedding_model="different-model")
    assert database.execute(
        "SELECT text, page_start, page_end, page_paragraph, sequence FROM public.passages"
    ).fetchone() == ("Synthetic paragraph.", 10, 10, 1, 1)
    assert database.execute(
        "SELECT count(*) FROM content_ingestion.rag_chunks"
    ).fetchone() == (1,)
    assert database.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='public.book_editions'::regclass ORDER BY conname"
    ).fetchall() == before
    assert database.execute("SELECT to_regclass('public.chapters')").fetchone() == (None,)
    store(database, book)
    assert database.execute("SELECT count(*) FROM public.book_editions").fetchone() == (1,)
    assert database.execute("SELECT count(*) FROM public.passages").fetchone() == (1,)
    assert database.execute("SELECT count(*) FROM content_ingestion.imports").fetchone() == (2,)


def test_checked_in_epub_satisfies_canonical_backend_locations(database):
    migrate(database)
    apply_schema(database, SCHEMA)
    book = parse_epub(Path(__file__).parents[2] / "books/egw/steps_to_christ.epub")
    embedded = tuple(
        replace(chunk, embedding=(0.01,) * EMBEDDING_DIMENSIONS)
        for chunk in build_chunks(book)
    )
    store_book(database, book, embedded, embedding_model=MODEL)
    assert imported(database, book)
    assert database.execute(
        "SELECT count(*), count(DISTINCT chapter_number), count(DISTINCT sequence) "
        "FROM public.passages WHERE is_active"
    ).fetchone() == (273, 13, 273)


def test_revision_replacement_preserves_ids_other_languages_and_approval_integrity(database):
    migrate(database)
    apply_schema(database, SCHEMA)
    original = document()
    extra = replace(original.chapters[0].passages[0], passage_id="removed",
                    paragraph_number=2, sequence=2)
    original = replace(original, chapters=(
        replace(original.chapters[0], passages=original.chapters[0].passages + (extra,)),
    ))
    store(database, original)
    store(database, document(language="es"))
    source = uuid.uuid4()
    database.execute(
        """
        INSERT INTO public.content_sources (
            source_id, book_id, language, category, canonical_uri, license,
            access_policy, approval_status, source_sha256
        ) VALUES (%s, 'synthetic', 'en', 'egw', 'https://example.org/test',
                  'Synthetic fixture', 'public', 'approved',
                  public.content_source_sha256('synthetic','en'))
        """, (source,),
    )
    database.commit()
    updated = document("v2", text="Changed synthetic paragraph.")
    store(database, updated)
    assert imported(database, updated)
    assert not imported(database, original)
    assert database.execute(
        "SELECT is_active FROM public.passages WHERE passage_id='removed'"
    ).fetchone() == (False,)
    assert database.execute(
        "SELECT text FROM public.passages WHERE passage_id=%s",
        (original.chapters[0].passages[0].passage_id,),
    ).fetchone() == ("Changed synthetic paragraph.",)
    assert database.execute(
        "SELECT count(*) FROM content_ingestion.editions WHERE language='en'"
    ).fetchone() == (2,)
    assert database.execute(
        "SELECT count(*) FROM content_ingestion.editions WHERE language='en' AND is_active"
    ).fetchone() == (1,)
    assert database.execute(
        "SELECT text FROM content_ingestion.rag_chunks WHERE language='es'"
    ).fetchone() == ("Synthetic paragraph.",)
    assert database.execute(
        "SELECT source_sha256=public.content_source_sha256(book_id,language) "
        "FROM public.content_sources WHERE source_id=%s", (source,),
    ).fetchone() == (False,)


def test_failure_rolls_back_reader_and_index_and_records_failed_attempt(database):
    migrate(database)
    apply_schema(database, SCHEMA)
    original = document()
    store(database, original)
    updated = document("v2", text="Must not replace current text.")
    invalid_chunk = replace(chunks(updated)[0], chapter_id="missing-chapter")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store_book(database, updated, (invalid_chunk,), embedding_model=MODEL)
    assert imported(database, original)
    assert database.execute("SELECT text FROM public.passages").fetchone() == (
        "Synthetic paragraph.",
    )
    assert database.execute("SELECT text FROM content_ingestion.rag_chunks").fetchone() == (
        "Synthetic paragraph.",
    )
    assert database.execute(
        "SELECT status, error_message IS NOT NULL FROM content_ingestion.imports "
        "WHERE source_sha256='v2'"
    ).fetchone() == ("failed", True)


def test_canonical_drift_invalidates_unchanged_import(database):
    migrate(database)
    apply_schema(database, SCHEMA)
    book = document()
    store(database, book)
    database.execute("UPDATE public.passages SET text='Changed outside ingestion'")
    assert not imported(database, book)


def test_passage_order_and_citation_updates_preserve_reader_identity(database):
    migrate(database)
    apply_schema(database, SCHEMA)
    original = document()
    store(database, original)
    first = replace(original.chapters[0].passages[0], sequence=2,
                    paragraph_number=2, page_start=11, page_end=12, page_paragraph=3)
    preceding = replace(first, passage_id="new-preceding", sequence=1, paragraph_number=1)
    updated = replace(
        original, source_sha256="v2", content_version="v2",
        chapters=(replace(original.chapters[0], passages=(preceding, first)),),
    )
    store(database, updated)
    assert database.execute(
        "SELECT paragraph_number, sequence, page_start, page_end, page_paragraph "
        "FROM public.passages WHERE passage_id=%s", (first.passage_id,),
    ).fetchone() == (2, 2, 11, 12, 3)


def test_foreign_passage_id_collision_is_explicit_and_rolled_back(database):
    migrate(database)
    apply_schema(database, SCHEMA)
    original = document()
    store(database, original)
    other = replace(original, book_id="another-work")
    with pytest.raises(ValueError, match="Passage ID belongs"):
        store(database, other)
    assert database.execute(
        "SELECT count(*) FROM public.books WHERE book_id='another-work'"
    ).fetchone() == (0,)
    assert imported(database, original)


def test_legacy_schema_is_rejected_without_conversion_or_data_loss(database):
    migrate(database)
    database.execute("ALTER TABLE public.book_editions ADD COLUMN edition_id TEXT")
    database.execute("INSERT INTO public.books VALUES ('sentinel','Sentinel','Test')")
    database.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="Incompatible legacy"):
        apply_schema(database, SCHEMA)
    assert database.execute("SELECT title FROM public.books").fetchone() == ("Sentinel",)
    assert database.execute("SELECT to_regnamespace('content_ingestion')").fetchone() == (None,)
