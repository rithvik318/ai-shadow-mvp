import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus


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
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    """One page of documents, with enough context to request the next."""

    items: list[DocumentResponse]
    total: int
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ErrorResponse(BaseModel):
    """The body returned for every mapped domain error."""

    detail: str
    error: str
