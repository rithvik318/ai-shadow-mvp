"""A deterministic stand-in for the embedding provider.

Applied automatically wherever ingestion runs, so that no test can reach the
network by accident — a test that silently started making real embedding calls
would be slow, costly, and dependent on a key nobody wants in CI.

Vectors are derived from the text, so identical text embeds identically and
different text does not. That is enough for the properties these tests assert
(a vector was stored, of the right width, against the right chunk) without
pretending to model semantic similarity.
"""

import hashlib
from collections.abc import Iterator

import pytest

from app.config.settings import settings
from app.services.llm.embedding_service import embedding_service


def deterministic_vector(text: str) -> list[float]:
    """Return a stable pseudo-vector of the configured width for `text`."""

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    dimensions = settings.EMBEDDING_DIMENSIONS

    return [(digest[index % len(digest)] - 128) / 128.0 for index in range(dimensions)]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """Replace the provider call, and record the batches it was asked for."""

    batches: list[list[str]] = []

    def embed_texts(texts: list[str]) -> list[list[float]]:
        batches.append(list(texts))
        return [deterministic_vector(text) for text in texts]

    monkeypatch.setattr(embedding_service, "embed_texts", embed_texts)
    yield batches
