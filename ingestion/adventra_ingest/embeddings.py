from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import replace
from typing import Protocol

from openai import AzureOpenAI

from .models import RagChunk

EMBEDDING_DIMENSIONS = 1536


class Embedder(Protocol):
    model_name: str

    def embed(self, chunks: tuple[RagChunk, ...]) -> tuple[RagChunk, ...]: ...


class AzureOpenAIEmbedder:
    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        api_key: str,
        api_version: str = "2024-02-01",
        batch_size: int = 16,
    ) -> None:
        if not endpoint or not deployment or not api_key:
            raise ValueError(
                "Azure OpenAI endpoint, embedding deployment, and API key are required"
            )
        self.model_name = deployment
        self._batch_size = batch_size
        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )

    @classmethod
    def from_environment(cls) -> "AzureOpenAIEmbedder":
        return cls(
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            deployment=os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )

    def embed(self, chunks: tuple[RagChunk, ...]) -> tuple[RagChunk, ...]:
        embedded: list[RagChunk] = []
        for batch in _batched(chunks, self._batch_size):
            response = self._client.embeddings.create(
                model=self.model_name,
                input=[chunk.text for chunk in batch],
                dimensions=EMBEDDING_DIMENSIONS,
            )
            vectors = sorted(response.data, key=lambda item: item.index)
            if len(vectors) != len(batch):
                raise RuntimeError(
                    "Azure OpenAI returned a different number of embeddings "
                    "than requested"
                )
            for chunk, item in zip(batch, vectors, strict=True):
                if len(item.embedding) != EMBEDDING_DIMENSIONS:
                    raise RuntimeError(
                        f"Expected {EMBEDDING_DIMENSIONS} embedding dimensions "
                        f"but received {len(item.embedding)}"
                    )
                embedded.append(replace(chunk, embedding=tuple(item.embedding)))
        return tuple(embedded)


def _batched(
    values: tuple[RagChunk, ...], size: int
) -> Iterable[tuple[RagChunk, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]

