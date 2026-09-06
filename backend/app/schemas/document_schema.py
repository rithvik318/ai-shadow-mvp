import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, IngestionResult


class DocumentResponse(BaseModel):
    """A document and the state of its ingestion."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    file_size_bytes: int
    page_count: int | None
    chunk_count: int
    status: DocumentStatus
    error_message: str | None
    content_hash: str | None
    source_uri: str | None
    source_version: str | None
    created_at: datetime
    updated_at: datetime


class CorpusStatsResponse(BaseModel):
    """How much is in the knowledge base, counted rather than sampled.

    Exists because the alternative was a client counting a page of documents
    and calling the result a corpus total. Every other fact a status screen
    needs — which folders are configured, when each last synchronised, what
    failed — is already answered by `GET /sync/onedrive/status`, so this adds
    only the counts nothing else could supply.
    """

    documents: int = Field(description="Documents in the shared knowledge base.")
    chunks: int = Field(description="Retrievable passages across every document.")
    embedded_chunks: int = Field(
        description=(
            "Passages that carry an embedding. Anything short of `chunks` is "
            "indexed but not yet searchable."
        )
    )
    by_status: dict[DocumentStatus, int] = Field(
        description="Document count per ingestion status, including zeroes."
    )


class DocumentListResponse(BaseModel):
    """One page of documents, with enough context to request the next."""

    items: list[DocumentResponse]
    total: int
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class BatchUploadItem(BaseModel):
    """What happened to one file in a batch, independently of the others."""

    filename: str
    result: IngestionResult
    succeeded: bool
    document_id: uuid.UUID | None = None
    status: DocumentStatus | None = None
    reason: str | None = None


class BatchUploadResponse(BaseModel):
    """The per-file results of one batch upload.

    The counts are derived from `items` and carried explicitly so a caller can
    tell a wholly successful batch from a partial one without walking the list.
    """

    items: list[BatchUploadItem]
    total: int
    succeeded: int
    failed: int


class ErrorResponse(BaseModel):
    """The body returned for every mapped domain error."""

    detail: str
    error: str
