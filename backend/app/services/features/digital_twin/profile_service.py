"""Reading and writing one user's Digital Twin profile.

One profile per user, upserted rather than created and updated separately: the
caller of `PUT /profile` does not know or care whether a row exists yet, and
making them find out first would be two round trips to express one intention.

`user_id` is a required argument on every function here, with no default. That
is deliberate: a default would be a global profile that a forgotten argument
could silently reach, and the isolation this module exists to provide would be
one missing keyword deep. If a caller cannot say whose profile it wants, it
does not get one.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ProfileIncompleteError, ProfileNotFoundError
from app.models.digital_twin import DigitalTwinProfile
from app.services.features.users import user_service

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


def find_profile(db: Session, *, user_id: uuid.UUID) -> DigitalTwinProfile | None:
    """Return this user's profile, or None when they have not set one up."""

    return db.execute(
        select(DigitalTwinProfile).where(DigitalTwinProfile.user_id == user_id)
    ).scalar_one_or_none()


def get_profile(db: Session, *, user_id: uuid.UUID) -> DigitalTwinProfile:
    """Return this user's profile, raising if they have none.

    The API wants a 404 for "no profile yet"; chat wants to carry on without
    one. `find_profile` serves the second case, so neither has to treat the
    other's normal state as an error.
    """

    profile = find_profile(db, user_id=user_id)

    if profile is None:
        raise ProfileNotFoundError("No Digital Twin profile has been set up yet.")

    return profile


# The two fields that name the same person in two tables. `users.name` and
# `users.role` are what the workspace displays — the twin switcher, the sidebar,
# the report header — and `digital_twin_profile.name` and `.role` are what the
# persona is written from. Nothing kept them in step, so editing the profile's
# name changed the persona and left every label in the product reading the old
# one, with no error to explain why.
IDENTITY_FIELDS = ("name", "role")


def _apply_to_identity(
    db: Session, supplied: dict[str, object], *, user_id: uuid.UUID
) -> None:
    """Write a renamed persona back onto the user record it describes.

    No commit: the caller's commit covers both rows, so a failure cannot leave
    a user renamed with an unsaved profile.

    Only fields the caller actually supplied, and only non-blank ones — the
    same rule the profile itself follows, so a partial edit cannot blank an
    identity that the form did not show.
    """

    user = user_service.get_user(db, user_id)

    for field in IDENTITY_FIELDS:
        value = supplied.get(field)

        if isinstance(value, str) and value.strip():
            setattr(user, field, value.strip())


def upsert_profile(
    db: Session, values: dict[str, object], *, user_id: uuid.UUID
) -> DigitalTwinProfile:
    """Create the profile, or replace the fields the caller supplied.

    Absent keys are left alone rather than blanked, so a caller correcting one
    field does not have to resend the whole profile to keep the rest.

    Writes only to `user_id`'s row. The owner is an argument, never something
    the supplied values can set — `EDITABLE_FIELDS` does not contain it, so a
    request body carrying `user_id` cannot redirect the write at somebody else.

    A supplied `name` or `role` is also written to that user's own record, so
    the name the product shows and the name the persona is written from cannot
    drift apart — see `_apply_to_identity`. Both rows are saved by one commit.
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

    _apply_to_identity(db, supplied, user_id=user_id)

    db.commit()
    db.refresh(profile)

    logger.info(
        "digital_twin_profile_saved",
        extra={"user_id": str(user_id), "fields": sorted(supplied)},
    )

    return profile
