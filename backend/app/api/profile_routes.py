from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.database.session import get_db
from app.schemas.digital_twin_schema import ProfileRequest, ProfileResponse
from app.schemas.document_schema import ErrorResponse
from app.services.features.digital_twin import profile_service

router = APIRouter(prefix="/profile", tags=["digital twin"])

IDENTITY_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "No X-User-ID header"},
    404: {"model": ErrorResponse, "description": "Unknown user"},
    422: {"model": ErrorResponse, "description": "X-User-ID is not a UUID"},
}


@router.get(
    "",
    response_model=ProfileResponse,
    summary="Read the current user's Digital Twin profile",
    responses={
        **IDENTITY_RESPONSES,
        404: {"model": ErrorResponse, "description": "Unknown user, or no profile"},
    },
)
def read_profile(user: CurrentUser, db: Session = Depends(get_db)) -> ProfileResponse:
    """Return the profile of the user named by `X-User-ID`.

    One user's profile is never a fallback for another's: the lookup is scoped
    to this user, so somebody with no profile gets a 404 rather than somebody
    else's answer.

    `404` when none has been set up: an absent profile is a different thing
    from an empty one, and a client that cannot tell them apart will show a
    form full of blanks as though someone had filled it in that way.
    """

    profile = profile_service.get_profile(db, user_id=user.id)

    return ProfileResponse.model_validate(profile)


@router.put(
    "",
    response_model=ProfileResponse,
    summary="Create or update the current user's Digital Twin profile",
    responses={
        **IDENTITY_RESPONSES,
        422: {
            "model": ErrorResponse,
            "description": "Invalid field, or a first write missing a required one",
        },
    },
)
def write_profile(
    request: ProfileRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """Create or update the profile of the user named by `X-User-ID`.

    Fields left out are kept, not blanked, so correcting one does not mean
    resending the rest.

    The owner comes from the header and only from the header. `ProfileRequest`
    has no `user_id` field, so a body cannot aim this write at another user —
    which is the property that has to hold once the header becomes a real
    session, or authentication will have arrived too late to matter.
    """

    profile = profile_service.upsert_profile(db, request.supplied(), user_id=user.id)

    return ProfileResponse.model_validate(profile)
