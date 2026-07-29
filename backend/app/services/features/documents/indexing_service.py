"""Attaching embedding vectors to stored chunks.

The single place that turns chunk text into stored vectors. Both paths use it:
ingestion calls it inline for a freshly parsed document, and the backfill calls
it for documents ingested before embeddings existed, or whose embedding step
failed.

Transaction policy: these functions mutate and flush but do not commit, except
`backfill_missing_embeddings`, which owns its loop and commits per document.
Committing is the orchestrator's job — ingestion needs the chunk writes and the
final document status in one transaction.
"""

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import EmbeddingError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.llm.embedding_service import embedding_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillResult:
    """Outcome of a backfill run."""

    documents_processed: int
    documents_failed: int
    chunks_embedded: int


def embed_document_chunks(
    db: Session,
    document_id: uuid.UUID,
    *,
    user_id: str = MVP_USER_ID,
) -> int:
    """Embed every chunk of one document that has no vector yet.

    Returns the number of chunks embedded. Idempotent: chunks that already have
    a vector are skipped, so a partially-embedded document can be resumed
    without re-embedding — and re-paying for — the chunks that succeeded.
    """

    chunks = (
        db.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.user_id == user_id,
                DocumentChunk.embedding.is_(None),
            )
            .order_by(DocumentChunk.chunk_index)
        )
        .scalars()
        .all()
    )

    if not chunks:
        return 0

    started = time.perf_counter()
    vectors = embedding_service.embed_texts([chunk.content for chunk in chunks])
    duration_ms = (time.perf_counter() - started) * 1000

    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector

    db.flush()

    logger.info(
        "document_chunks_embedded",
        extra={
            "document_id": str(document_id),
            "chunk_count": len(chunks),
            "embedding_duration_ms": round(duration_ms, 2),
        },
    )

    return len(chunks)


def count_unembedded_chunks(db: Session, *, user_id: str = MVP_USER_ID) -> int:
    """Return how many stored chunks are still missing a vector."""

    return len(
        db.execute(
            select(DocumentChunk.id).where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.embedding.is_(None),
            )
        )
        .scalars()
        .all()
    )


def backfill_missing_embeddings(
    db: Session,
    *,
    user_id: str = MVP_USER_ID,
    limit: int = 100,
) -> BackfillResult:
    """Embed chunks for documents that have none, one document at a time.

    Documents already ingested when embeddings did not exist have chunks but no
    vectors, and are invisible to retrieval until this runs. Each document is
    committed on its own so that one provider failure does not discard the
    documents already completed in the run.
    """

    # Only ids are selected: loading whole chunk rows to discover which
    # documents need work would pull every chunk's text into memory.
    document_ids = (
        db.execute(
            select(DocumentChunk.document_id)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.embedding.is_(None),
            )
            .distinct()
            .limit(limit)
        )
        .scalars()
        .all()
    )

    processed = 0
    failed = 0
    embedded = 0

    for document_id in document_ids:
        try:
            embedded += embed_document_chunks(db, document_id, user_id=user_id)
        except EmbeddingError as exc:
            db.rollback()
            failed += 1
            logger.warning(
                "backfill_document_failed",
                extra={"document_id": str(document_id), "reason": type(exc).__name__},
            )
            continue

        document = db.get(Document, document_id)
        if document is not None and document.status != DocumentStatus.INDEXED:
            document.status = DocumentStatus.INDEXED
            document.error_message = None

        db.commit()
        processed += 1

    logger.info(
        "embedding_backfill_completed",
        extra={
            "documents_processed": processed,
            "documents_failed": failed,
            "chunks_embedded": embedded,
        },
    )

    return BackfillResult(
        documents_processed=processed,
        documents_failed=failed,
        chunks_embedded=embedded,
    )
