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
    assert len(book.chapters) == 13
    assert [chapter.number for chapter in book.chapters] == list(range(1, 14))
    assert all(chapter.title.casefold() != "foreword" for chapter in book.chapters)
    assert sum(len(chapter.passages) for chapter in book.chapters) > 100


def test_reader_locations_have_positive_chapters_and_book_wide_sequence() -> None:
    book = parse_epub(SAMPLE_BOOK)
    passages = [passage for chapter in book.chapters for passage in chapter.passages]
    assert all(p.chapter_number > 0 and p.paragraph_number > 0 for p in passages)
    assert [p.sequence for p in passages] == list(range(1, len(passages) + 1))
    assert book.chapters[0].passages[0].passage_id.endswith("chapter-001:p0001")


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


def test_passages_carry_printed_page_citations() -> None:
    book = parse_epub(SAMPLE_BOOK)
    chapter_one = next(chapter for chapter in book.chapters if chapter.number == 1)
    citations = [
        (passage.page_start, passage.page_paragraph, passage.page_end)
        for passage in chapter_one.passages[:5]
    ]

    assert citations == [(9, 1, 9), (9, 2, 9), (9, 3, 10), (10, 1, 10), (10, 2, 10)]
    assert chapter_one.passages[3].text.startswith("“God is love”")
    assert all(
        passage.page_start is not None and passage.page_paragraph is not None
        for chapter in book.chapters
        for passage in chapter.passages
    )


def test_quotation_source_lines_join_their_paragraph() -> None:
    book = parse_epub(SAMPLE_BOOK)
    chapter_one = next(chapter for chapter in book.chapters if chapter.number == 1)

    assert chapter_one.passages[1].text.endswith("Psalm 145:15, 16 .")
    assert not any(
        passage.text.startswith("Psalm 145")
        for chapter in book.chapters
        for passage in chapter.passages
    )
