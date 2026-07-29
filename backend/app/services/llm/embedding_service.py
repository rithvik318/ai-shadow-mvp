"""Provider-agnostic embedding generation.

Lives in the LLM layer, alongside `LLMService`, because generating a vector is
a provider capability rather than product logic: nothing here knows what a
document or a chunk is. Feature services hand it strings and receive vectors.
"""

import logging

from openai import OpenAIError

from app.config.settings import settings
from app.core.exceptions import EmbeddingDimensionError, EmbeddingError
from app.services.llm.client import get_embedding_client

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Turn text into vectors using the configured embedding provider.

    Requests are batched, because embedding a 200-chunk document one call at a
    time is 200 round trips for work the provider accepts in a handful.
    """

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, returning vectors in the same order.

        Raises `EmbeddingError` if the provider call fails, and
        `EmbeddingDimensionError` if it returns vectors of an unexpected width.
        """

        if not texts:
            return []

        if any(not text.strip() for text in texts):
            raise EmbeddingError("Cannot embed empty or whitespace-only text.")

        batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)
        vectors: list[list[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(self._embed_batch(batch))

        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single text, such as a search query."""

        return self.embed_texts([text])[0]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = get_embedding_client().embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=batch,
            )
        except OpenAIError as exc:
            logger.warning(
                "embedding_request_failed",
                extra={"batch_size": len(batch), "model": settings.EMBEDDING_MODEL},
            )
            raise EmbeddingError(f"Embedding provider call failed: {exc}") from exc

        # The API documents `data` as returned in input order, but each item
        # also carries its index. Sorting on it costs nothing and removes the
        # need to trust that, because a silent reordering would attach every
        # vector to the wrong chunk.
        items = sorted(response.data, key=lambda item: item.index)

        if len(items) != len(batch):
            raise EmbeddingError(
                f"Embedding provider returned {len(items)} vectors for "
                f"{len(batch)} inputs."
            )

        vectors = [list(item.embedding) for item in items]

        for vector in vectors:
            if len(vector) != settings.EMBEDDING_DIMENSIONS:
                raise EmbeddingDimensionError(
                    f"Embedding model '{settings.EMBEDDING_MODEL}' returned "
                    f"{len(vector)} dimensions, but the column is "
                    f"{settings.EMBEDDING_DIMENSIONS}. Change EMBEDDING_MODEL, "
                    f"or migrate the column width."
                )

        return vectors


embedding_service = EmbeddingService()
