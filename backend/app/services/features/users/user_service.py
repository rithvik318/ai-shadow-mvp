"""Creating and finding the people the Shadow can answer for.

Create and list, and nothing else. There is no update and no delete: an
identity that other rows point at should not be editable through the same API
that exists to make two test users, and deleting one would cascade a Digital
Twin away behind a single request.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DuplicateUserError, UserNotFoundError
from app.models.user import User

logger = logging.getLogger(__name__)


def create_user(db: Session, *, name: str, email: str, role: str) -> User:
    """Add a user, or raise if the email is taken.

    The uniqueness check is the database's, not a prior SELECT: two concurrent
    requests both find nothing and both insert, and only the constraint catches
    it. Translating the `IntegrityError` here keeps that a 409 rather than a
    500.
    """

    user = User(name=name.strip(), email=email.strip().lower(), role=role.strip())
    db.add(user)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateUserError(f"A user with email {email} already exists.") from exc

    db.refresh(user)

    logger.info(
        "user_created",
        extra={"user_id": str(user.id), "role": user.role},
    )

    return user


def list_users(db: Session) -> list[User]:
    """Every user, oldest first, so the list is stable between calls."""

    return list(
        db.execute(select(User).order_by(User.created_at, User.id)).scalars().all()
    )


def find_user(db: Session, user_id: uuid.UUID) -> User | None:
    """Return the user, or None. The caller decides whether that is an error."""

    return db.get(User, user_id)


def get_user(db: Session, user_id: uuid.UUID) -> User:
    """Return the user, or raise `UserNotFoundError`."""

    user = find_user(db, user_id)

    if user is None:
        raise UserNotFoundError(f"No user with id {user_id}.")

    return user
