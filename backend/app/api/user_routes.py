import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentAdmin
from app.database.session import get_db
from app.schemas.document_schema import ErrorResponse
from app.schemas.user_schema import (
    UserCreateRequest,
    UserDeletionPreview,
    UserDeletionResponse,
    UserListResponse,
    UserResponse,
)
from app.services.features.users import deletion_service, user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=UserListResponse,
    summary="List the people the Shadow can answer for",
)
def list_users(db: Session = Depends(get_db)) -> UserListResponse:
    """Every user, oldest first.

    Each `id` here is what goes in the `X-User-ID` header on `/profile`,
    `/memory` and `/chat`.
    """

    users = user_service.list_users(db)

    return UserListResponse(
        items=[UserResponse.model_validate(user) for user in users],
        total=len(users),
    )


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a person the Shadow can answer for",
    responses={
        409: {"model": ErrorResponse, "description": "That email already exists"},
        422: {"model": ErrorResponse, "description": "Invalid user"},
    },
)
def create_user(
    request: UserCreateRequest,
    db: Session = Depends(get_db),
) -> UserResponse:
    """Create a user.

    An identity, not an account: there is no password here and no session,
    because `X-User-ID` is a development stand-in for authentication rather
    than an implementation of it. Editing and deleting are deliberately absent
    — a Digital Twin hangs off these rows, and neither belongs behind the
    endpoint whose job is to make two test users.
    """

    user = user_service.create_user(
        db, name=request.name, email=request.email, role=request.role
    )

    return UserResponse.model_validate(user)


_SHARED_KNOWLEDGE_NOTE = (
    "The company knowledge base is shared and is not owned by this person. "
    "No documents or indexed passages are removed by deleting a user."
)

_DELETION_RESPONSES = {
    403: {"model": ErrorResponse, "description": "Only an administrator may do this"},
    404: {"model": ErrorResponse, "description": "No such user"},
}


@router.get(
    "/{user_id}/deletion-preview",
    response_model=UserDeletionPreview,
    responses=_DELETION_RESPONSES,
    summary="What deleting this user would remove",
)
def deletion_preview(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    db: Session = Depends(get_db),
) -> UserDeletionPreview:
    """Count what would be destroyed, without destroying anything.

    The confirmation dialog is built from this rather than from a hard-coded
    list, so the warning and the deletion cannot drift apart — both come from
    the same set of models.
    """

    user = user_service.get_user(db, user_id)
    owned = deletion_service.owned_row_counts(db, user_id)
    documents, _ = deletion_service.shared_knowledge_counts(db)

    return UserDeletionPreview(
        user_id=user.id,
        name=user.name,
        email=user.email,
        owned=owned,
        owned_total=sum(owned.values()),
        shared_knowledge_documents=documents,
        shared_knowledge_note=_SHARED_KNOWLEDGE_NOTE,
    )


@router.delete(
    "/{user_id}",
    response_model=UserDeletionResponse,
    responses=_DELETION_RESPONSES,
    summary="Delete a user and everything that is theirs",
)
def delete_user(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    db: Session = Depends(get_db),
) -> UserDeletionResponse:
    """Delete a person, transactionally.

    Administrator only — the single operation in this API that acts on somebody
    other than the caller, and therefore the only one that needs a permission
    check rather than user scoping.

    An administrator may delete themselves; that is a decision they are
    entitled to make, and refusing it would mean a deployment can reach a state
    where an account cannot be removed at all.

    The shared knowledge base is untouched. `shared_knowledge_documents` is
    returned so a caller can verify that from the response rather than trusting
    this docstring.
    """

    summary = deletion_service.delete_user(db, user_id)
    documents, _ = deletion_service.shared_knowledge_counts(db)

    return UserDeletionResponse(
        user_id=summary.user_id,
        deleted=summary.deleted,
        total=summary.total,
        shared_knowledge_documents=documents,
    )
