import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
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

IDENTITY_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "No X-User-ID header"},
    404: {"model": ErrorResponse, "description": "Unknown user"},
    422: {"model": ErrorResponse, "description": "X-User-ID is not a UUID"},
}


@router.get(
    "",
    response_model=MemoryListResponse,
    summary="List the current user's memories",
    responses=IDENTITY_RESPONSES,
)
def list_memories(
    user: CurrentUser,
    db: Session = Depends(get_db),
    type: MemoryType | None = Query(default=None, description="Filter by memory type"),
    active: bool | None = Query(default=None, description="Filter by active flag"),
) -> MemoryListResponse:
    """This user's memories matching the filters, most important first.

    Scoped to the user named by `X-User-ID`; another person's memories are not
    in this list under any filter.

    Unfiltered by default, including retired memories — this is the management
    view. What chat actually sees is the active, unexpired, capped subset.
    """

    memories = memory_service.list_memories(
        db, memory_type=type, active=active, user_id=user.id
    )

    return MemoryListResponse(
        items=[MemoryResponse.model_validate(memory) for memory in memories],
        total=len(memories),
    )


@router.post(
    "",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store a memory",
    responses={
        **IDENTITY_RESPONSES,
        422: {"model": ErrorResponse, "description": "Invalid memory"},
    },
)
def create_memory(
    request: MemoryCreateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> MemoryResponse:
    """Store one durable thing worth remembering, owned by the current user.

    The owner comes from `X-User-ID`; `MemoryCreateRequest` has no `user_id`
    field, so a body cannot file a memory under somebody else.

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
        user_id=user.id,
    )

    return MemoryResponse.model_validate(memory)


@router.patch(
    "/{memory_id}",
    response_model=MemoryResponse,
    summary="Update a memory",
    responses={
        **IDENTITY_RESPONSES,
        404: {"model": ErrorResponse, "description": "Unknown memory or user"},
        422: {"model": ErrorResponse, "description": "Invalid field"},
    },
)
def update_memory(
    memory_id: uuid.UUID,
    request: MemoryUpdateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> MemoryResponse:
    """Change the fields supplied. Set `active` to false to retire a memory.

    Another user's memory is a `404`, exactly as an unknown id is. Refusing
    with a `403` would confirm that the memory exists, which is a fact about
    somebody else's Digital Twin.
    """

    memory = memory_service.update_memory(
        db, memory_id, request.supplied(), user_id=user.id
    )

    return MemoryResponse.model_validate(memory)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory",
    responses={
        **IDENTITY_RESPONSES,
        404: {"model": ErrorResponse, "description": "Unknown memory or user"},
    },
)
def delete_memory(
    memory_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Response:
    """Remove one of this user's memories permanently.

    Another user's memory is a `404`, and is not deleted.

    Retiring is usually what is wanted — `PATCH` with `active: false` keeps the
    record of a decision that no longer applies. This is for the memory that
    should never have been stored.
    """

    memory_service.delete_memory(db, memory_id, user_id=user.id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
