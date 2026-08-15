"""Users are identities, not accounts."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import DuplicateUserError, UserNotFoundError
from app.services.features.users.user_service import (
    create_user,
    find_user,
    get_user,
    list_users,
)


def test_a_user_can_be_created(db_session: Session) -> None:
    user = create_user(db_session, name="Test CEO", email="ceo@example.com", role="CEO")

    assert user.name == "Test CEO"
    assert user.role == "CEO"
    assert isinstance(user.id, uuid.UUID)


def test_email_is_stored_lowercased(db_session: Session) -> None:
    """So that two spellings of one address cannot become two users."""

    user = create_user(db_session, name="Test CEO", email="CEO@Example.com", role="CEO")

    assert user.email == "ceo@example.com"


def test_a_duplicate_email_raises(db_session: Session) -> None:
    create_user(db_session, name="Test CEO", email="ceo@example.com", role="CEO")

    with pytest.raises(DuplicateUserError):
        create_user(db_session, name="Other", email="ceo@example.com", role="CEO")


def test_the_session_is_usable_after_a_duplicate(db_session: Session) -> None:
    """The rollback matters: without it the failed insert poisons the session
    and the next write fails for a reason that has nothing to do with itself."""

    create_user(db_session, name="Test CEO", email="ceo@example.com", role="CEO")

    with pytest.raises(DuplicateUserError):
        create_user(db_session, name="Other", email="ceo@example.com", role="CEO")

    later = create_user(
        db_session, name="Test CRM", email="crm@example.com", role="CRM Manager"
    )

    assert later.id is not None


def test_every_user_is_listed(db_session: Session) -> None:
    """Order is by creation time and then id — stable between calls, but not
    asserted as insertion order here: the database clock resolves to the
    second, so two users made in one test share a timestamp and the tie is
    broken on a random UUID."""

    first = create_user(
        db_session, name="Test CEO", email="ceo@example.com", role="CEO"
    )
    second = create_user(
        db_session, name="Test CRM", email="crm@example.com", role="CRM Manager"
    )

    listed = list_users(db_session)

    assert {user.id for user in listed} == {first.id, second.id}
    assert [user.id for user in list_users(db_session)] == [user.id for user in listed]


def test_an_unknown_user_is_not_found(db_session: Session) -> None:
    assert find_user(db_session, uuid.uuid4()) is None

    with pytest.raises(UserNotFoundError):
        get_user(db_session, uuid.uuid4())
