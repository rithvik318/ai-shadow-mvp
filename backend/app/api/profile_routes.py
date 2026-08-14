from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.digital_twin_schema import ProfileRequest, ProfileResponse
from app.schemas.document_schema import ErrorResponse
from app.services.features.digital_twin import profile_service

router = APIRouter(prefix="/profile", tags=["digital twin"])


@router.get(
    "",
    response_model=ProfileResponse,
    summary="Read the Digital Twin profile",
    responses={404: {"model": ErrorResponse, "description": "No profile yet"}},
)
def read_profile(db: Session = Depends(get_db)) -> ProfileResponse:
    """Return the profile the Shadow answers for.

    `404` when none has been set up: an absent profile is a different thing
    from an empty one, and a client that cannot tell them apart will show a
    form full of blanks as though someone had filled it in that way.
    """

    return ProfileResponse.model_validate(profile_service.get_profile(db))


@router.put(
    "",
    response_model=ProfileResponse,
    summary="Create or update the Digital Twin profile",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Invalid field, or a first write missing a required one",
        }
    },
)
def write_profile(
    request: ProfileRequest,
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """Create the profile, or update the fields supplied.

    Fields left out are kept, not blanked, so correcting one does not mean
    resending the rest. There is exactly one profile in this MVP; multi-user
    profile management is deliberately not built.
    """

    profile = profile_service.upsert_profile(db, request.supplied())

    return ProfileResponse.model_validate(profile)
