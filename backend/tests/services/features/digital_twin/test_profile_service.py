"""The profile is who the Shadow answers for. There is exactly one per user."""

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import ProfileIncompleteError, ProfileNotFoundError
from app.models.user import User
from app.services.features.digital_twin.profile_service import (
    find_profile,
    get_profile,
    upsert_profile,
)

EXECUTIVE = {
    "name": "Test Executive",
    "role": "CEO",
    "organization": "SunRadia",
    "communication_style": "Concise and executive-friendly",
    "responsibilities": ["Business development", "Strategic partnerships"],
    "expertise": ["Data modernization", "Analytics"],
    "priorities": ["Government opportunities", "Enterprise AI"],
    "decision_preferences": [
        "Prefer evidence-backed recommendations",
        "Avoid unnecessary detail",
    ],
    "current_focus": ["Freddie Mac analytics discussion"],
}

MANAGER = {
    "name": "Test CRM Manager",
    "role": "CRM Manager",
    "organization": "SunRadia",
    "communication_style": "Operational and detailed",
    "priorities": ["Pipeline management"],
    "current_focus": ["Customer follow-ups"],
}


def test_no_profile_is_a_normal_state(db_session: Session, test_user: User) -> None:
    """Chat has to work before anyone has set one up."""

    assert find_profile(db_session, user_id=test_user.id) is None


def test_reading_an_absent_profile_raises(db_session: Session, test_user: User) -> None:
    """The API wants a 404 here, which is a different need from chat's."""

    with pytest.raises(ProfileNotFoundError):
        get_profile(db_session, user_id=test_user.id)


def test_a_profile_can_be_created(db_session: Session, test_user: User) -> None:
    profile = upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)

    assert profile.name == "Test Executive"
    assert profile.role == "CEO"
    assert profile.user_id == test_user.id
    assert profile.priorities == ["Government opportunities", "Enterprise AI"]


def test_a_created_profile_can_be_read_back(
    db_session: Session, test_user: User
) -> None:
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)

    stored = get_profile(db_session, user_id=test_user.id)

    assert stored.organization == "SunRadia"
    assert stored.expertise == ["Data modernization", "Analytics"]


def test_updating_leaves_untouched_fields_alone(
    db_session: Session, test_user: User
) -> None:
    """Correcting one field must not blank the rest — the alternative is a
    caller that has to read, merge and resend the whole profile to fix a typo,
    and one that forgets is one that silently erases it."""

    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)

    updated = upsert_profile(
        db_session, {"role": "Chief Executive Officer"}, user_id=test_user.id
    )

    assert updated.role == "Chief Executive Officer"
    assert updated.name == "Test Executive"
    assert updated.priorities == ["Government opportunities", "Enterprise AI"]


def test_updating_does_not_create_a_second_profile(
    db_session: Session, test_user: User
) -> None:
    """One profile per user, or answers stop being reproducible depending on
    which row was read."""

    from app.models.digital_twin import DigitalTwinProfile

    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    upsert_profile(db_session, {"name": "Someone Else"}, user_id=test_user.id)

    assert db_session.query(DigitalTwinProfile).count() == 1


def test_a_first_write_without_a_name_is_rejected(
    db_session: Session, test_user: User
) -> None:
    """The column is NOT NULL; failing here gives the caller a 422 instead of
    an integrity error surfacing as a 500."""

    with pytest.raises(ProfileIncompleteError):
        upsert_profile(
            db_session,
            {"role": "CEO", "organization": "SunRadia"},
            user_id=test_user.id,
        )


def test_unknown_fields_are_ignored(db_session: Session, test_user: User) -> None:
    """The writable set is a list, not "whatever the model has" — otherwise a
    column added later becomes settable through the API by accident, and
    `user_id` is the one that must never be."""

    profile = upsert_profile(
        db_session,
        {**EXECUTIVE, "user_id": "someone-else"},
        user_id=test_user.id,
    )

    assert profile.user_id == test_user.id


# --- isolation ------------------------------------------------------------


def test_two_users_have_separate_profiles(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    upsert_profile(db_session, dict(MANAGER), user_id=test_user_b.id)

    assert get_profile(db_session, user_id=test_user.id).role == "CEO"
    assert get_profile(db_session, user_id=test_user_b.id).role == "CRM Manager"


def test_one_users_profile_is_never_anothers_fallback(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """A user with no profile gets nothing — not the profile that happens to
    exist. Silently answering as somebody else is the failure this whole
    module is shaped to prevent."""

    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)

    assert find_profile(db_session, user_id=test_user_b.id) is None

    with pytest.raises(ProfileNotFoundError):
        get_profile(db_session, user_id=test_user_b.id)


def test_writing_one_users_profile_does_not_touch_anothers(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    upsert_profile(db_session, dict(MANAGER), user_id=test_user_b.id)

    upsert_profile(db_session, {"name": "Renamed"}, user_id=test_user_b.id)

    assert get_profile(db_session, user_id=test_user.id).name == "Test Executive"
    assert get_profile(db_session, user_id=test_user_b.id).name == "Renamed"
