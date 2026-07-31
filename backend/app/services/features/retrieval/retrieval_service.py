"""Semantic search over stored document chunks.

Embeds the query through the existing `EmbeddingService`, then ranks chunks by
cosine distance in the database rather than in Python — the HNSW index created
in migration 0001 exists precisely so this never becomes a table scan plus an
application-side sort.

**Similarity metric: cosine.** Chosen for four reasons, in descending order of
how much they would hurt to get wrong:

1. The HNSW index in migration 0001 is built `USING hnsw (embedding
   vector_cosine_ops)`. An index is only usable by the operator class it was
   built for, so searching with L2 (`<->`) or inner product (`<#>`) would
   silently fall back to a sequential scan — correct answers, quietly
   catastrophic latency once the corpus grows.
2. Cosine is invariant to vector magnitude, so a long chunk is not scored
   differently from a short one purely for having a larger norm. L2 on
   unnormalised vectors conflates "different meaning" with "different length",
   which is exactly the wrong bias for retrieval over chunks that vary in size.
3. It is bounded to `[-1, 1]`, which makes the configurable threshold a number
   a human can reason about and carry between models. An L2 threshold is
   unbounded and specific to one embedding space.
4. The default model, `openai/text-embedding-3-small`, returns unit-normalised
   vectors, for which cosine and inner product rank identically — so cosine
   costs nothing here and stays correct if a model that does not normalise is
   swapped in later.

Changing the metric therefore means changing the index too. See
docs/DECISIONS.md.

This service knows nothing about prompts, chat or citations. It answers one
question — which stored chunks are closest to this query — and returns them
with the provenance needed to cite them later.
"""

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import EmptyQueryError, RetrievalError
from app.database.vector import cosine_distance
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.llm.embedding_service import embedding_service

logger = logging.getLogger(__name__)


class _Unset:
    """Sentinel distinguishing "argument omitted" from "explicitly disabled".

    `similarity_threshold=None` has to mean "no floor at all". Without a
    sentinel it would be indistinguishable from "not supplied", and there would
    be no way to disable a floor that configuration had turned on.
    """

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()

# An upper bound on how far down the ranking deduplication will walk before
# giving up on filling `top_k`. A corpus that is mostly repeated boilerplate
# would otherwise page through everything. Hitting it is logged rather than
# passed over in silence.
_MAX_CANDIDATE_MULTIPLIER = 10
_MIN_CANDIDATE_CEILING = 100


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk matched by a search, with everything needed to cite it."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    similarity: float
    page_number: int | None = None
    section_title: str | None = None


def _resolve_threshold(
    similarity_threshold: float | None | _Unset,
) -> float | None:
    """Return the effective floor, or None when filtering is disabled."""

    if isinstance(similarity_threshold, _Unset):
        return settings.RETRIEVAL_SIMILARITY_THRESHOLD

    return similarity_threshold


def _validate(top_k: int, threshold: float | None) -> None:
    if top_k <= 0:
        raise RetrievalError("top_k must be greater than zero.")

    if threshold is not None and not -1.0 <= threshold <= 1.0:
        raise RetrievalError(
            "similarity_threshold must be between -1.0 and 1.0, the range of "
            "cosine similarity, or None to disable filtering."
        )


def _corpus_has_embeddings(db: Session, user_id: str) -> bool:
    """Whether this user has any chunk that could be searched at all."""

    return (
        db.execute(
            select(DocumentChunk.id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.embedding.is_not(None),
                Document.status == DocumentStatus.INDEXED,
            )
            .limit(1)
        ).first()
        is not None
    )


def search(
    db: Session,
    query: str,
    *,
    user_id: str = MVP_USER_ID,
    top_k: int | None = None,
    similarity_threshold: float | None | _Unset = UNSET,
) -> list[RetrievedChunk]:
    """Return the chunks most similar to `query`, most similar first.

    `top_k` and `similarity_threshold` default to `RETRIEVAL_TOP_K` and
    `RETRIEVAL_SIMILARITY_THRESHOLD`. Passing `similarity_threshold=None`
    disables the floor for this call; setting the configured value to None
    disables it everywhere.

    Only chunks belonging to `user_id`, carrying a vector, and whose document
    is `indexed` are eligible: a document mid-ingestion or one whose embedding
    step failed would otherwise contribute a partial, misleading answer.

    Raises `EmptyQueryError` for a blank query and `RetrievalError` for invalid
    arguments. `EmbeddingError` from the provider propagates unchanged, so a
    provider outage is not mistaken for "nothing found".
    """

    if not query or not query.strip():
        raise EmptyQueryError("Search query cannot be empty.")

    effective_top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
    threshold = _resolve_threshold(similarity_threshold)
    _validate(effective_top_k, threshold)

    # Checked before embedding, not after: an empty corpus is a normal state on
    # a fresh install, and it should not cost a provider call — nor fail with a
    # provider error when no key is configured yet.
    if not _corpus_has_embeddings(db, user_id):
        logger.info("retrieval_skipped_empty_corpus", extra={"user_id": user_id})
        return []

    started = time.perf_counter()
    query_vector = embedding_service.embed_query(query.strip())
    embed_ms = (time.perf_counter() - started) * 1000

    distance = cosine_distance(DocumentChunk.embedding, query_vector)

    predicates = [
        DocumentChunk.user_id == user_id,
        DocumentChunk.embedding.is_not(None),
        Document.status == DocumentStatus.INDEXED,
        # Excluded explicitly rather than as a side effect of the threshold,
        # because the threshold can be disabled. A stored vector of the wrong
        # width or of zero length has no defined similarity, and SQLite and
        # Postgres sort NULLs to opposite ends.
        distance.is_not(None),
    ]

    if threshold is not None:
        # A higher similarity floor is a tighter distance ceiling.
        predicates.append(distance <= 1.0 - threshold)

    statement = (
        select(DocumentChunk, Document.filename, distance.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(*predicates)
        # `id` breaks ties deterministically. Without it, two chunks at equal
        # distance could swap between pages and be seen twice or not at all.
        .order_by(distance, DocumentChunk.id)
    )

    results: list[RetrievedChunk] = []
    seen_content: set[str] = set()
    page_size = max(effective_top_k * 2, 10)
    ceiling = max(effective_top_k * _MAX_CANDIDATE_MULTIPLIER, _MIN_CANDIDATE_CEILING)
    examined = 0
    exhausted = False

    started = time.perf_counter()

    # Paged rather than a single over-fetch: deduplication must not be the
    # reason a caller receives fewer chunks than it asked for, and no fixed
    # multiple of `top_k` can guarantee that against an unknown number of
    # duplicates. Most searches still complete in one round trip.
    while len(results) < effective_top_k and examined < ceiling:
        rows = db.execute(statement.limit(page_size).offset(examined)).all()

        if not rows:
            exhausted = True
            break

        examined += len(rows)

        for chunk, filename, chunk_distance in rows:
            if chunk.content in seen_content:
                continue

            seen_content.add(chunk.content)
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    filename=filename,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    similarity=1.0 - float(chunk_distance),
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                )
            )

            if len(results) == effective_top_k:
                break

        if len(rows) < page_size:
            exhausted = True
            break

    search_ms = (time.perf_counter() - started) * 1000

    if len(results) < effective_top_k and not exhausted:
        logger.warning(
            "retrieval_truncated_by_candidate_ceiling",
            extra={
                "user_id": user_id,
                "top_k": effective_top_k,
                "results_returned": len(results),
                "candidates_examined": examined,
                "ceiling": ceiling,
            },
        )

    logger.info(
        "retrieval_completed",
        extra={
            "user_id": user_id,
            "top_k": effective_top_k,
            "similarity_threshold": threshold,
            "candidates_examined": examined,
            "results_returned": len(results),
            "embedding_duration_ms": round(embed_ms, 2),
            "search_duration_ms": round(search_ms, 2),
        },
    )

    return results
