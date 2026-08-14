"""Reading and writing the single Digital Twin profile.

One profile per owner, upserted rather than created and updated separately:
the caller of `PUT /profile` does not know or care whether a row exists yet,
and making them find out first would be two round trips to express one
intention.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import ProfileIncompleteError, ProfileNotFoundError
from app.models.digital_twin import DigitalTwinProfile

logger = logging.getLogger(__name__)

# Without these a profile names nobody, and the column is NOT NULL. Required on
# the first write only: a later edit of one field must not have to resend them.
REQUIRED_ON_CREATE = ("name", "role", "organization")

# The fields a caller may set. Listing them explicitly keeps a future column —
# an internal flag, say — from being writable through the API by accident.
EDITABLE_FIELDS = (
    "name",
    "role",
    "organization",
    "communication_style",
    "responsibilities",
    "expertise",
    "priorities",
    "decision_preferences",
    "current_focus",
)


def find_profile(
    db: Session, *, user_id: str = MVP_USER_ID
) -> DigitalTwinProfile | None:
    """Return the owner's profile, or None when none has been set up."""

    return db.execute(
        select(DigitalTwinProfile).where(DigitalTwinProfile.user_id == user_id)
    ).scalar_one_or_none()


def get_profile(db: Session, *, user_id: str = MVP_USER_ID) -> DigitalTwinProfile:
    """Return the owner's profile, raising if there is none.

    The API wants a 404 for "no profile yet"; chat wants to carry on without
    one. `find_profile` serves the second case, so neither has to treat the
    other's normal state as an error.
    """

    profile = find_profile(db, user_id=user_id)

    if profile is None:
        raise ProfileNotFoundError("No Digital Twin profile has been set up yet.")

    return profile


def upsert_profile(
    db: Session, values: dict[str, object], *, user_id: str = MVP_USER_ID
) -> DigitalTwinProfile:
    """Create the profile, or replace the fields the caller supplied.

    Absent keys are left alone rather than blanked, so a caller correcting one
    field does not have to resend the whole profile to keep the rest.
    """

    profile = find_profile(db, user_id=user_id)
    supplied = {
        field: value for field, value in values.items() if field in EDITABLE_FIELDS
    }

    if profile is None:
        missing = [field for field in REQUIRED_ON_CREATE if not supplied.get(field)]
        if missing:
            raise ProfileIncompleteError(
                "A new profile needs " + ", ".join(missing) + "."
            )

        profile = DigitalTwinProfile(user_id=user_id, **supplied)
        db.add(profile)
    else:
        for field, value in supplied.items():
            setattr(profile, field, value)

    db.commit()
    db.refresh(profile)

    logger.info(
        "digital_twin_profile_saved",
        extra={"user_id": user_id, "fields": sorted(supplied)},
    )

    return profile
