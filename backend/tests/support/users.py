"""Shared user fixtures.

Two users, because one proves nothing about isolation. Every test that needs a
Digital Twin needs somebody to own it, and building that inline thirty times
would make the interesting part of each test — who can see what — the least
visible thing in it.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.models.user import User


def _make(db: Session, *, name: str, email: str, role: str) -> User:
    user = User(name=name, email=email, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)

    return user


@pytest.fixture
def test_user(db_session: Session) -> Iterator[User]:
    """The user most tests act as."""

    yield _make(db_session, name="Test CEO", email="test-ceo@example.com", role="CEO")


@pytest.fixture
def test_user_b(db_session: Session) -> Iterator[User]:
    """A second user, who must never see the first one's Digital Twin."""

    yield _make(
        db_session,
        name="Test CRM Manager",
        email="test-crm@example.com",
        role="CRM Manager",
    )


@pytest.fixture
def user_headers(test_user: User) -> dict[str, str]:
    """The MVP identity header for `test_user`."""

    return {"X-User-ID": str(test_user.id)}


@pytest.fixture
def user_b_headers(test_user_b: User) -> dict[str, str]:
    """The MVP identity header for `test_user_b`."""

    return {"X-User-ID": str(test_user_b.id)}
