"""The profile is who the Shadow answers for. There is exactly one."""

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import ProfileIncompleteError, ProfileNotFoundError
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


def test_no_profile_is_a_normal_state(db_session: Session) -> None:
    """Chat has to work before anyone has set one up."""

    assert find_profile(db_session) is None


def test_reading_an_absent_profile_raises(db_session: Session) -> None:
    """The API wants a 404 here, which is a different need from chat's."""

    with pytest.raises(ProfileNotFoundError):
        get_profile(db_session)


def test_a_profile_can_be_created(db_session: Session) -> None:
    profile = upsert_profile(db_session, dict(EXECUTIVE))

    assert profile.name == "Test Executive"
    assert profile.role == "CEO"
    assert profile.priorities == ["Government opportunities", "Enterprise AI"]


def test_a_created_profile_can_be_read_back(db_session: Session) -> None:
    upsert_profile(db_session, dict(EXECUTIVE))

    stored = get_profile(db_session)

    assert stored.organization == "SunRadia"
    assert stored.expertise == ["Data modernization", "Analytics"]


def test_updating_leaves_untouched_fields_alone(db_session: Session) -> None:
    """Correcting one field must not blank the rest — the alternative is a
    caller that has to read, merge and resend the whole profile to fix a typo,
    and one that forgets is one that silently erases it."""

    upsert_profile(db_session, dict(EXECUTIVE))

    updated = upsert_profile(db_session, {"role": "Chief Executive Officer"})

    assert updated.role == "Chief Executive Officer"
    assert updated.name == "Test Executive"
    assert updated.priorities == ["Government opportunities", "Enterprise AI"]


def test_updating_does_not_create_a_second_profile(db_session: Session) -> None:
    """ "The active profile" has to be singular, or answers stop being
    reproducible depending on which row was read."""

    from app.models.digital_twin import DigitalTwinProfile

    upsert_profile(db_session, dict(EXECUTIVE))
    upsert_profile(db_session, {"name": "Someone Else"})

    assert db_session.query(DigitalTwinProfile).count() == 1


def test_a_first_write_without_a_name_is_rejected(db_session: Session) -> None:
    """The column is NOT NULL; failing here gives the caller a 422 instead of
    an integrity error surfacing as a 500."""

    with pytest.raises(ProfileIncompleteError):
        upsert_profile(db_session, {"role": "CEO", "organization": "SunRadia"})


def test_unknown_fields_are_ignored(db_session: Session) -> None:
    """The writable set is a list, not "whatever the model has" — otherwise a
    column added later becomes settable through the API by accident."""

    profile = upsert_profile(db_session, {**EXECUTIVE, "user_id": "someone-else"})

    assert profile.user_id == "mvp-user"
