import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.document import DocumentStatus
from app.schemas.document_schema import (
    DocumentListResponse,
    DocumentResponse,
    ErrorResponse,
)
from app.services.features.documents import document_service, ingestion_service

router = APIRouter(prefix="/documents", tags=["documents"])

_ERROR_RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "Document not found"},
    413: {"model": ErrorResponse, "description": "File exceeds the size limit"},
    415: {"model": ErrorResponse, "description": "Unsupported document type"},
    422: {"model": ErrorResponse, "description": "File is empty or unreadable"},
}


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest a document",
    responses={key: value for key, value in _ERROR_RESPONSES.items() if key != 404},
)
async def upload_document(
    file: UploadFile = File(..., description="PDF, DOCX, TXT or Markdown file"),
    db: Session = Depends(get_db),
) -> DocumentResponse:
    """Ingest one document: validate, extract text, chunk, and persist.

    Returns the document with `status="indexed"` and its chunk count on
    success. Ingestion is synchronous, so the response reflects the final
    state rather than a queued job.
    """

    data = await file.read()

    document = ingestion_service.ingest_document(
        db,
        data=data,
        filename=file.filename or "",
        content_type=file.content_type,
    )

    return DocumentResponse.model_validate(document)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List ingested documents",
)
def list_documents(
    db: Session = Depends(get_db),
    document_status: DocumentStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by ingestion status",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    """Return the caller's documents, newest first."""

    documents, total = document_service.list_documents(
        db, status=document_status, limit=limit, offset=offset
    )

    return DocumentListResponse(
        items=[DocumentResponse.model_validate(doc) for doc in documents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get one document",
    responses={404: _ERROR_RESPONSES[404]},
)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DocumentResponse:
    """Return a single document, including its failure reason if ingestion
    did not succeed."""

    return DocumentResponse.model_validate(
        document_service.get_document(db, document_id)
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its chunks",
    responses={404: _ERROR_RESPONSES[404]},
)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Delete a document. Its chunks are removed by cascade."""

    document_service.delete_document(db, document_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
