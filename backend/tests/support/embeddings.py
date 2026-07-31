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
from collections.abc import Callable, Iterator

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


@pytest.fixture
def embed_query_as(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[list[float]], list[str]]]:
    """Pin the vector a query embeds to, and record the queries embedded.

    Retrieval and chat tests need exact control over the query vector: the
    ordering assertions are only meaningful if the geometry is chosen rather
    than hashed. The returned list captures every query text sent to the
    provider, so tests can assert a call was — or was not — made.
    """

    embedded: list[str] = []

    def install(vector: list[float]) -> list[str]:
        def embed_query(text: str) -> list[float]:
            embedded.append(text)
            return list(vector)

        monkeypatch.setattr(embedding_service, "embed_query", embed_query)
        return embedded

    yield install
