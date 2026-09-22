from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    passage_id: str
    chapter_id: str
    chapter_number: int
    paragraph_number: int
    sequence: int
    text: str
    content_hash: str


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    number: int
    title: str
    passages: tuple[Passage, ...]


@dataclass(frozen=True)
class BookDocument:
    book_id: str
    title: str
    author: str
    language: str
    source_identifier: str | None
    publisher: str | None
    source_path: str
    source_sha256: str
    content_version: str
    chapters: tuple[Chapter, ...]


@dataclass(frozen=True)
class RagChunk:
    chunk_id: str
    chapter_id: str
    chapter_number: int
    chunk_number: int
    passage_ids: tuple[str, ...]
    title: str
    text: str
    token_count: int
    content_hash: str
    embedding: tuple[float, ...] = ()

