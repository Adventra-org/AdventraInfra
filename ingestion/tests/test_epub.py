from pathlib import Path

from adventra_ingest.chunking import build_chunks
from adventra_ingest.epub import parse_epub

SAMPLE_BOOK = (
    Path(__file__).parents[2] / "books" / "egw" / "steps_to_christ.epub"
)


def test_parses_sample_book_metadata_and_content() -> None:
    book = parse_epub(SAMPLE_BOOK, SAMPLE_BOOK.parents[2])

    assert book.book_id == "steps-to-christ"
    assert book.title == "Steps to Christ"
    assert book.author == "Ellen G. White"
    assert book.language == "en"
    assert book.source_path == "books/egw/steps_to_christ.epub"
    assert len(book.chapters) == 14
    assert sum(len(chapter.passages) for chapter in book.chapters) > 100


def test_passage_ids_are_stable() -> None:
    first = parse_epub(SAMPLE_BOOK)
    second = parse_epub(SAMPLE_BOOK)

    assert [
        passage.passage_id
        for chapter in first.chapters
        for passage in chapter.passages
    ] == [
        passage.passage_id
        for chapter in second.chapters
        for passage in chapter.passages
    ]


def test_builds_bounded_chunks_with_passage_citations() -> None:
    book = parse_epub(SAMPLE_BOOK)
    chunks = build_chunks(book)

    assert chunks
    assert all(chunk.passage_ids for chunk in chunks)
    assert all(chunk.token_count <= 800 for chunk in chunks)
    assert all(chunk.chunk_id.startswith("steps-to-christ:en:") for chunk in chunks)
    assert all(
        current.passage_ids[-1] != following.passage_ids[-1]
        for current, following in zip(chunks, chunks[1:])
        if current.chapter_id == following.chapter_id
    )
