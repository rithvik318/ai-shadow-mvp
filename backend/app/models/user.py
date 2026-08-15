"""The people the Shadow can answer for.

A row here is an identity, not an account: there is no password, no session and
no permission. It exists so that a profile and a set of memories can belong to
somebody, and so that the boundary between two people is enforced by a foreign
key rather than by a convention nobody can check.

Real authentication replaces how a request is *attributed* to one of these
rows. It does not change the rows themselves, which is the point of separating
them now rather than after the email and task workflows are built on top.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class User(Base):
    """One person the Shadow can represent."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Unique because it is the only human-readable handle on a row whose id is
    # a UUID — without it, two "CRM Manager" users are indistinguishable to the
    # person creating them.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # A plain string, deliberately. A role table would be the first half of an
    # RBAC system nobody has asked for; today this labels the persona and
    # nothing branches on it.
    role: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)
