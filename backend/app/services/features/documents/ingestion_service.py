"""Orchestration of the upload pipeline: validate → parse → chunk → persist.

Ingestion is synchronous. At MVP document sizes this keeps the request model
simple and the failure modes visible; moving it to a background worker is
tracked in docs/KNOWN_ISSUES.md.
"""

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import (
    DocumentError,
    DocumentTooLargeError,
    EmptyDocumentError,
)
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.documents.chunker_service import chunk_document
from app.services.features.documents.parser_service import (
    parse_document,
    resolve_format,
)


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


def ingest_document(
    db: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    user_id: str = MVP_USER_ID,
) -> Document:
    """Ingest one uploaded file and return the persisted document.

    Validation failures raise before anything is written. Parse and chunk
    failures mark the document `failed` with its error recorded, commit that
    state, and re-raise — so a failed ingest is visible through the API rather
    than silently absent.
    """

    validate_upload(data, filename, content_type)

    document = Document(
        user_id=user_id,
        filename=filename,
        content_type=content_type or "application/octet-stream",
        file_size_bytes=len(data),
        status=DocumentStatus.PROCESSING,
        chunk_count=0,
    )
    db.add(document)
    db.flush()

    try:
        parsed = parse_document(data, filename, content_type)
        chunks = chunk_document(parsed)
    except DocumentError as exc:
        document.status = DocumentStatus.FAILED
        document.error_message = str(exc)
        db.commit()
        db.refresh(document)
        raise

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
    document.status = DocumentStatus.INDEXED

    db.commit()
    db.refresh(document)

    return document
