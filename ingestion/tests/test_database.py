from unittest.mock import MagicMock

from psycopg import Connection

from adventra_ingest.database import store_book
from adventra_ingest.embeddings import EMBEDDING_DIMENSIONS
from adventra_ingest.models import BookDocument, Chapter, Passage, RagChunk


def test_store_book_uses_cursor_for_batch_inserts() -> None:
    connection = MagicMock(spec=Connection)
    cursor = connection.cursor.return_value.__enter__.return_value
    book = _book()
    chunk = RagChunk(
        chunk_id="sample:en:chapter-001:c0001",
        chapter_id=book.chapters[0].chapter_id,
        chapter_number=1,
        chunk_number=1,
        passage_ids=(book.chapters[0].passages[0].passage_id,),
        title="Chapter 1",
        text="A short paragraph.",
        token_count=4,
        content_hash="chunk-hash",
        embedding=(0.0,) * EMBEDDING_DIMENSIONS,
    )

    store_book(
        connection,
        book,
        (chunk,),
        embedding_model="text-embedding-3-small",
    )

    assert connection.cursor.call_count == 2
    assert cursor.executemany.call_count == 2


def _book() -> BookDocument:
    chapter_id = "sample:en:chapter-001"
    passage = Passage(
        passage_id=f"{chapter_id}:p0001",
        chapter_id=chapter_id,
        chapter_number=1,
        paragraph_number=1,
        sequence=1,
        text="A short paragraph.",
        content_hash="passage-hash",
    )
    return BookDocument(
        book_id="sample",
        title="Sample",
        author="Author",
        language="en",
        source_identifier=None,
        publisher=None,
        source_path="books/sample.epub",
        source_sha256="source-sha",
        content_version="version",
        chapters=(
            Chapter(
                chapter_id=chapter_id,
                number=1,
                title="Chapter 1",
                passages=(passage,),
            ),
        ),
    )
