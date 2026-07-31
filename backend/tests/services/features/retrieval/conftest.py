from collections.abc import Callable, Iterator

import pytest

from app.services.llm.embedding_service import embedding_service
from tests.support.database import db_session  # noqa: F401

__all__ = ["db_session", "embed_query_as"]


@pytest.fixture
def embed_query_as(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[list[float]], list[str]]]:
    """Pin the vector a query embeds to, and record the queries embedded.

    Retrieval tests need exact control over the query vector: the ordering
    assertions are only meaningful if the geometry is chosen rather than
    hashed. The returned list captures every query text sent to the provider,
    so tests can assert a call was — or was not — made.
    """

    embedded: list[str] = []

    def install(vector: list[float]) -> list[str]:
        def embed_query(text: str) -> list[float]:
            embedded.append(text)
            return list(vector)

        monkeypatch.setattr(embedding_service, "embed_query", embed_query)
        return embedded

    yield install
