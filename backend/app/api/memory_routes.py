import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.digital_twin import MemoryType
from app.schemas.digital_twin_schema import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdateRequest,
)
from app.schemas.document_schema import ErrorResponse
from app.services.features.digital_twin import memory_service

router = APIRouter(prefix="/memory", tags=["digital twin"])


@router.get(
    "",
    response_model=MemoryListResponse,
    summary="List stored memories",
)
def list_memories(
    db: Session = Depends(get_db),
    type: MemoryType | None = Query(default=None, description="Filter by memory type"),
    active: bool | None = Query(default=None, description="Filter by active flag"),
) -> MemoryListResponse:
    """Every memory matching the filters, most important first.

    Unfiltered by default, including retired memories — this is the management
    view. What chat actually sees is the active, unexpired, capped subset.
    """

    memories = memory_service.list_memories(db, memory_type=type, active=active)

    return MemoryListResponse(
        items=[MemoryResponse.model_validate(memory) for memory in memories],
        total=len(memories),
    )


@router.post(
    "",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store a memory",
    responses={422: {"model": ErrorResponse, "description": "Invalid memory"}},
)
def create_memory(
    request: MemoryCreateRequest,
    db: Session = Depends(get_db),
) -> MemoryResponse:
    """Store one durable thing worth remembering.

    Memories are written here and nowhere else. Nothing is extracted
    automatically from chat or from ingested documents, which is what keeps
    this a memory rather than a transcript.
    """

    memory = memory_service.create_memory(
        db,
        memory_type=request.type,
        content=request.content,
        importance=request.importance,
        source=request.source,
        expires_at=request.expires_at,
    )

    return MemoryResponse.model_validate(memory)


@router.patch(
    "/{memory_id}",
    response_model=MemoryResponse,
    summary="Update a memory",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown memory"},
        422: {"model": ErrorResponse, "description": "Invalid field"},
    },
)
def update_memory(
    memory_id: uuid.UUID,
    request: MemoryUpdateRequest,
    db: Session = Depends(get_db),
) -> MemoryResponse:
    """Change the fields supplied. Set `active` to false to retire a memory."""

    memory = memory_service.update_memory(db, memory_id, request.supplied())

    return MemoryResponse.model_validate(memory)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory",
    responses={404: {"model": ErrorResponse, "description": "Unknown memory"}},
)
def delete_memory(memory_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """Remove a memory permanently.

    Retiring is usually what is wanted — `PATCH` with `active: false` keeps the
    record of a decision that no longer applies. This is for the memory that
    should never have been stored.
    """

    memory_service.delete_memory(db, memory_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
