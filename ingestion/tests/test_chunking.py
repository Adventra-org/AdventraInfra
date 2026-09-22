from adventra_ingest.chunking import build_chunks
from adventra_ingest.models import BookDocument, Chapter, Passage


def test_rejects_invalid_chunk_limits() -> None:
    book = _book()

    try:
        build_chunks(book, target_tokens=100, max_tokens=50)
    except ValueError as error:
        assert "overlap < target <= max" in str(error)
    else:
        raise AssertionError("Expected invalid chunk limits to fail")


def _book() -> BookDocument:
    chapter_id = "sample:en:chapter-001"
    passage = Passage(
        passage_id=f"{chapter_id}:p0001",
        chapter_id=chapter_id,
        chapter_number=1,
        paragraph_number=1,
        sequence=1,
        text="A short paragraph.",
        content_hash="hash",
    )
    return BookDocument(
        book_id="sample",
        title="Sample",
        author="Author",
        language="en",
        source_identifier=None,
        publisher=None,
        source_path="sample.epub",
        source_sha256="sha",
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

