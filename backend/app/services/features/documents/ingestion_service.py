"""Orchestration of the upload pipeline: validate → identify → parse → chunk → embed.

Ingestion is synchronous. At MVP document sizes this keeps the request model
simple and the failure modes visible; moving it to a background worker is
tracked in docs/KNOWN_ISSUES.md.

There is exactly one ingestion path. `ingest_document` is the raising form used
by the single-file endpoint; `ingest_file` is the same work reported as a value
instead of an exception, which is what batch upload — and, later, OneDrive
synchronisation — needs in order to keep one file's failure from ending the
run. Both call `_ingest`. Adding a second caller must not mean adding a second
pipeline.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import (
    DocumentError,
    DocumentTooLargeError,
    EmbeddingError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)
from app.models.document import (
    SUCCESSFUL_INGESTION_RESULTS,
    Document,
    DocumentChunk,
    DocumentStatus,
    IngestionResult,
)
from app.services.features.documents.chunker_service import chunk_document
from app.services.features.documents.indexing_service import embed_document_chunks
from app.services.features.documents.parser_service import (
    parse_document,
    resolve_format,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionOutcome:
    """The result of ingesting one file, as a value rather than an exception."""

    filename: str
    result: IngestionResult
    document_id: uuid.UUID | None = None
    status: DocumentStatus | None = None
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.result in SUCCESSFUL_INGESTION_RESULTS


def content_digest(data: bytes) -> str:
    """Return the identity of a file's contents."""

    return hashlib.sha256(data).hexdigest()


def validate_upload(data: bytes, filename: str, content_type: str | None) -> None:
    """Reject an upload before any database row is created.

    Ordered cheapest-first: emptiness, then size, then format. Failures here
    leave no trace, unlike parse failures, which are recorded against a
    persisted document so the user can see why ingestion failed.
    """

    if not filename or not filename.strip():
        raise EmptyDocumentError("Uploaded file has no filename.")

    if not data:
        raise EmptyDocumentError("Uploaded file is empty.")

    if len(data) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise DocumentTooLargeError(
            f"File is {len(data)} bytes, which exceeds the maximum of "
            f"{settings.MAX_UPLOAD_SIZE_BYTES} bytes."
        )

    resolve_format(filename, content_type)


def _find_existing(
    db: Session,
    *,
    user_id: str,
    source_uri: str | None,
    digest: str | None,
) -> Document | None:
    """Find the document this upload is another copy or version of.

    With a source, identity is the source: the same file at the same place is
    the same document however much its contents changed. Without one, identity
    can only be the contents, so an edited file uploaded by hand is a new
    document — there is nothing in a plain upload that ties it to the earlier
    one. Filename is never consulted, in either branch.
    """

    if source_uri is not None:
        return db.execute(
            select(Document).where(
                Document.user_id == user_id,
                Document.source_uri == source_uri,
            )
        ).scalar_one_or_none()

    if digest is None:
        return None

    # `source_uri IS NULL` keeps a hand upload from adopting a synced
    # document that happens to hold the same bytes, which would then be
    # re-pointed at a source it did not come from.
    return (
        db.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
                Document.content_hash == digest,
                Document.source_uri.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
        .scalars()
        .first()
    )


def _drop_chunks(db: Session, document: Document) -> None:
    """Remove every stored chunk of a document.

    A bulk delete rather than a cascade: the document row survives, and only
    its indexed representation is being replaced. `synchronize_session=False`
    with an explicit expiry keeps the session from serving the deleted rows
    back out of its identity map.
    """

    db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == document.id),
        execution_options={"synchronize_session": False},
    )
    db.flush()
    db.expire(document, ["chunks"])


def _ingest(
    db: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    user_id: str,
    source_uri: str | None,
    source_version: str | None,
) -> tuple[Document, IngestionResult, DocumentError | EmbeddingError | None]:
    """Do the work, and report a post-persistence failure rather than raise it.

    Validation failures still raise: they happen before a document exists, and
    there is nothing to report them against. Everything after that point is
    recorded on the document and returned, so that both public entry points can
    decide for themselves whether to raise.
    """

    validate_upload(data, filename, content_type)

    digest = content_digest(data)
    existing = _find_existing(db, user_id=user_id, source_uri=source_uri, digest=digest)

    if (
        existing is not None
        and existing.content_hash == digest
        and existing.status is DocumentStatus.INDEXED
    ):
        # Same bytes, already indexed. Re-parsing and re-embedding would cost
        # money to produce the rows that are already there.
        logger.info(
            "document_ingest_skipped_unchanged",
            extra={"document_id": str(existing.id), "filename": filename},
        )
        return existing, IngestionResult.UNCHANGED, None

    if existing is None:
        document = Document(
            user_id=user_id,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            file_size_bytes=len(data),
            content_hash=digest,
            source_uri=source_uri,
            source_version=source_version,
            status=DocumentStatus.PROCESSING,
            chunk_count=0,
        )
        db.add(document)
        db.flush()
        result = IngestionResult.INDEXED
    else:
        document = existing
        document.filename = filename
        document.content_type = content_type or "application/octet-stream"
        document.file_size_bytes = len(data)
        document.content_hash = digest
        document.source_version = source_version
        document.status = DocumentStatus.PROCESSING
        document.error_message = None
        db.flush()
        result = IngestionResult.REPLACED

    try:
        parsed = parse_document(data, filename, content_type)
        chunks = chunk_document(parsed)
    except DocumentError as exc:
        # Deliberately before the old chunks are dropped. A re-index whose new
        # content will not parse leaves the previous chunks in place rather
        # than emptying the document — and the `failed` status keeps them out
        # of retrieval either way, so nothing stale is reachable.
        document.status = DocumentStatus.FAILED
        document.error_message = str(exc)
        db.commit()
        db.refresh(document)
        return document, IngestionResult.FAILED, exc

    if result is IngestionResult.REPLACED:
        _drop_chunks(db, document)

    db.add_all(
        DocumentChunk(
            document_id=document.id,
            user_id=user_id,
            chunk_index=chunk.index,
            content=chunk.content,
            char_count=chunk.char_count,
            page_number=chunk.page_number,
            section_title=chunk.section_title,
        )
        for chunk in chunks
    )

    document.page_count = parsed.page_count
    document.chunk_count = len(chunks)
    db.flush()

    try:
        embed_document_chunks(db, document.id, user_id=user_id)
    except EmbeddingError as exc:
        # The chunks are kept. A document is only `indexed` once its chunks
        # carry vectors, because a document without them is invisible to
        # retrieval and reporting it as indexed would make that look like "no
        # relevant results". Keeping them lets `backfill_missing_embeddings`
        # finish the job later without re-parsing the file.
        document.status = DocumentStatus.FAILED
        document.error_message = str(exc)
        db.commit()
        db.refresh(document)
        return document, IngestionResult.FAILED, exc

    document.status = DocumentStatus.INDEXED
    document.error_message = None

    db.commit()
    db.refresh(document)

    logger.info(
        "document_ingested",
        extra={
            "document_id": str(document.id),
            "result": result.value,
            "chunk_count": document.chunk_count,
            "page_count": document.page_count,
            "file_size_bytes": document.file_size_bytes,
        },
    )

    return document, result, None


def ingest_document(
    db: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    user_id: str = MVP_USER_ID,
    source_uri: str | None = None,
    source_version: str | None = None,
) -> Document:
    """Ingest one uploaded file and return the persisted document.

    Validation failures raise before anything is written. Parse, chunk and
    embedding failures mark the document `failed` with its error recorded,
    commit that state, and re-raise — so a failed ingest is visible through the
    API rather than silently absent.

    Re-uploading bytes already indexed returns the existing document without
    re-parsing or re-embedding it.
    """

    document, _result, error = _ingest(
        db,
        data=data,
        filename=filename,
        content_type=content_type,
        user_id=user_id,
        source_uri=source_uri,
        source_version=source_version,
    )

    if error is not None:
        raise error

    return document


def _record_unsupported(
    db: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    user_id: str,
    source_uri: str | None,
    source_version: str | None,
    reason: str,
) -> IngestionOutcome:
    """Report an unsupported file, persisting it only if it has a source.

    A hand upload of a `.doc` leaves no row: the person is told 415 and can
    convert it. A *synced* `.doc` is different — without a record, every sync
    run would rediscover and re-reject it forever, and nothing would be able to
    show which corpus files the knowledge base is missing.
    """

    if source_uri is None:
        return IngestionOutcome(
            filename=filename, result=IngestionResult.UNSUPPORTED, reason=reason
        )

    document = _find_existing(db, user_id=user_id, source_uri=source_uri, digest=None)

    if document is None:
        document = Document(
            user_id=user_id,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            file_size_bytes=len(data),
            content_hash=content_digest(data) if data else None,
            source_uri=source_uri,
            source_version=source_version,
            status=DocumentStatus.UNSUPPORTED,
            chunk_count=0,
            error_message=reason,
        )
        db.add(document)
    else:
        # A document that was supported and has become unsupported — renamed,
        # or replaced with a legacy format — must not keep serving its old
        # chunks under a status that says it holds none.
        _drop_chunks(db, document)
        document.filename = filename
        document.content_type = content_type or "application/octet-stream"
        document.file_size_bytes = len(data)
        document.content_hash = content_digest(data) if data else None
        document.source_version = source_version
        document.status = DocumentStatus.UNSUPPORTED
        document.error_message = reason
        document.chunk_count = 0

    db.commit()
    db.refresh(document)

    return IngestionOutcome(
        filename=filename,
        result=IngestionResult.UNSUPPORTED,
        document_id=document.id,
        status=document.status,
        reason=reason,
    )


def ingest_file(
    db: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    user_id: str = MVP_USER_ID,
    source_uri: str | None = None,
    source_version: str | None = None,
) -> IngestionOutcome:
    """Ingest one file and describe what happened, without raising.

    The entry point for anything ingesting more than one file. A caller
    processing a batch cannot use exceptions for this: the first bad file
    would end the run, and the files after it — which are fine — would never
    be attempted.
    """

    try:
        document, result, error = _ingest(
            db,
            data=data,
            filename=filename,
            content_type=content_type,
            user_id=user_id,
            source_uri=source_uri,
            source_version=source_version,
        )
    except UnsupportedDocumentTypeError as exc:
        return _record_unsupported(
            db,
            data=data,
            filename=filename,
            content_type=content_type,
            user_id=user_id,
            source_uri=source_uri,
            source_version=source_version,
            reason=str(exc),
        )
    except DocumentError as exc:
        # Empty or oversized: rejected before a row existed, so there is
        # nothing to roll back and nothing to point at.
        return IngestionOutcome(
            filename=filename, result=IngestionResult.FAILED, reason=str(exc)
        )
    except Exception as exc:  # noqa: BLE001 - one file must not end the batch
        # Nothing above is expected to raise anything else. If it does, the
        # session may be mid-transaction and unusable for the files after this
        # one, so it is reset here. The reason names the error type and no
        # more: a traceback is for the log, not for the response body.
        db.rollback()
        logger.exception("document_ingest_unexpected_error", extra={"file": filename})
        return IngestionOutcome(
            filename=filename,
            result=IngestionResult.FAILED,
            reason=f"Ingestion failed unexpectedly ({type(exc).__name__}).",
        )

    if error is not None:
        return IngestionOutcome(
            filename=filename,
            result=IngestionResult.FAILED,
            document_id=document.id,
            status=document.status,
            reason=str(error),
        )

    return IngestionOutcome(
        filename=filename,
        result=result,
        document_id=document.id,
        status=document.status,
    )
