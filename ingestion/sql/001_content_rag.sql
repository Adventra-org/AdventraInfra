-- Reader tables belong exclusively to AdventraBackend migrations.
DO $$
BEGIN
    IF to_regclass('public.books') IS NULL
        OR to_regclass('public.book_editions') IS NULL
        OR to_regclass('public.passages') IS NULL
        OR to_regprocedure('public.content_source_sha256(text,text)') IS NULL THEN
        RAISE EXCEPTION 'Apply all AdventraBackend migrations before running ingestion';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'book_editions'
            AND column_name = 'edition_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.book_editions'::regclass AND contype = 'p'
            AND pg_get_constraintdef(oid) = 'PRIMARY KEY (book_id, language)'
    ) THEN
        RAISE EXCEPTION 'Incompatible legacy ingestion schema: reconcile with Backend migrations before importing; no automatic conversion or data deletion is performed';
    END IF;
END;
$$;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS content_ingestion;

CREATE TABLE IF NOT EXISTS content_ingestion.imports (
    import_id UUID PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'succeeded', 'failed')),
    book_id TEXT NOT NULL,
    language TEXT NOT NULL,
    chapter_count INTEGER NOT NULL DEFAULT 0,
    passage_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_imports_source_sha256
    ON content_ingestion.imports(source_sha256);

CREATE TABLE IF NOT EXISTS content_ingestion.editions (
    edition_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    language TEXT NOT NULL,
    source_identifier TEXT,
    publisher TEXT,
    source_format TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    content_version TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    import_id UUID NOT NULL REFERENCES content_ingestion.imports(import_id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (book_id, language)
        REFERENCES public.book_editions(book_id, language)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_editions_one_active_language
    ON content_ingestion.editions(book_id, language) WHERE is_active;

CREATE TABLE IF NOT EXISTS content_ingestion.chapters (
    chapter_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES content_ingestion.editions(edition_id),
    book_id TEXT NOT NULL,
    language TEXT NOT NULL,
    chapter_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (book_id, language, chapter_number),
    FOREIGN KEY (book_id, language)
        REFERENCES public.book_editions(book_id, language)
);

CREATE TABLE IF NOT EXISTS content_ingestion.rag_chunks (
    chunk_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES content_ingestion.editions(edition_id),
    chapter_id TEXT NOT NULL REFERENCES content_ingestion.chapters(chapter_id),
    book_id TEXT NOT NULL,
    language TEXT NOT NULL,
    chunk_number INTEGER NOT NULL,
    passage_ids TEXT[] NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions = 1536),
    content_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (book_id, language, chapter_id, chunk_number),
    FOREIGN KEY (book_id, language)
        REFERENCES public.book_editions(book_id, language)
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_book_language
    ON content_ingestion.rag_chunks(book_id, language);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding_hnsw
    ON content_ingestion.rag_chunks USING HNSW (embedding vector_cosine_ops);
