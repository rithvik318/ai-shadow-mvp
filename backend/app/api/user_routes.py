from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.document_schema import ErrorResponse
from app.schemas.user_schema import (
    UserCreateRequest,
    UserListResponse,
    UserResponse,
)
from app.services.features.users import user_service

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
