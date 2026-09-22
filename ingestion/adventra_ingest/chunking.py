from __future__ import annotations

import hashlib

import tiktoken

from .models import BookDocument, Chapter, Passage, RagChunk

_ENCODING = tiktoken.get_encoding("cl100k_base")


def build_chunks(
    book: BookDocument,
    *,
    target_tokens: int = 600,
    max_tokens: int = 800,
    overlap_tokens: int = 80,
) -> tuple[RagChunk, ...]:
    if not 0 <= overlap_tokens < target_tokens <= max_tokens:
        raise ValueError("Expected 0 <= overlap < target <= max tokens")

    chunks: list[RagChunk] = []
    for chapter in book.chapters:
        groups = _group_passages(
            chapter,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
        for chunk_number, group in enumerate(groups, start=1):
            text = "\n\n".join(passage.text for passage in group)
            chunk_id = f"{chapter.chapter_id}:chunk-{chunk_number:04d}"
            chunks.append(
                RagChunk(
                    chunk_id=chunk_id,
                    chapter_id=chapter.chapter_id,
                    chapter_number=chapter.number,
                    chunk_number=chunk_number,
                    passage_ids=tuple(passage.passage_id for passage in group),
                    title=f"{book.title} — {chapter.title}",
                    text=text,
                    token_count=count_tokens(text),
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    return tuple(chunks)


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def _group_passages(
    chapter: Chapter,
    *,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> list[list[Passage]]:
    groups: list[list[Passage]] = []
    current: list[Passage] = []
    current_tokens = 0

    for passage in chapter.passages:
        passage_tokens = count_tokens(passage.text)
        if passage_tokens > max_tokens:
            raise ValueError(
                f"Passage {passage.passage_id} has {passage_tokens} tokens; "
                f"maximum supported passage size is {max_tokens}"
            )
        if current and current_tokens + passage_tokens > max_tokens:
            groups.append(current)
            current = _overlap(current, overlap_tokens)
            current_tokens = sum(count_tokens(item.text) for item in current)
        current.append(passage)
        current_tokens += passage_tokens
        if current_tokens >= target_tokens:
            groups.append(current)
            current = _overlap(current, overlap_tokens)
            current_tokens = sum(count_tokens(item.text) for item in current)

    if current and (
        not groups
        or current[-1].passage_id != groups[-1][-1].passage_id
    ):
        groups.append(current)
    return groups


def _overlap(passages: list[Passage], overlap_tokens: int) -> list[Passage]:
    if overlap_tokens == 0:
        return []
    result: list[Passage] = []
    total = 0
    for passage in reversed(passages):
        result.insert(0, passage)
        total += count_tokens(passage.text)
        if total >= overlap_tokens:
            break
    return result
