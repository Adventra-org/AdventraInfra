CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS content_imports (
    import_id UUID PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'succeeded', 'failed')),
    book_id TEXT,
    language TEXT,
    chapter_count INTEGER NOT NULL DEFAULT 0,
    passage_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_content_imports_source_sha256
    ON content_imports(source_sha256);

CREATE TABLE IF NOT EXISTS books (
    book_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    source_identifier TEXT,
    publisher TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS book_editions (
    edition_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    source_format TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    content_version TEXT NOT NULL,
    import_id UUID NOT NULL REFERENCES content_imports(import_id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (book_id, language, source_sha256)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_book_editions_one_active_language
    ON book_editions(book_id, language)
    WHERE is_active;

CREATE TABLE IF NOT EXISTS chapters (
    chapter_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES book_editions(edition_id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    chapter_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (book_id, language, chapter_number)
);

CREATE INDEX IF NOT EXISTS idx_chapters_book_language
    ON chapters(book_id, language, chapter_number)
    WHERE is_active;

CREATE TABLE IF NOT EXISTS passages (
    passage_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES book_editions(edition_id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(chapter_id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    chapter_number INTEGER NOT NULL,
    paragraph_number INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    passage_type TEXT NOT NULL DEFAULT 'paragraph',
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', COALESCE(text, ''))
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (book_id, language, chapter_number, paragraph_number)
);

CREATE INDEX IF NOT EXISTS idx_passages_chapter_sequence
    ON passages(chapter_id, sequence)
    WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_passages_book_language
    ON passages(book_id, language)
    WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_passages_search_vector
    ON passages USING GIN(search_vector);

CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES book_editions(edition_id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(chapter_id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (book_id, language, chapter_id, chunk_number)
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_book_language
    ON rag_chunks(book_id, language);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding_hnsw
    ON rag_chunks USING HNSW (embedding vector_cosine_ops);

