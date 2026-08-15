"""The person the Shadow answers for, and what it durably knows about them.

Two tables, deliberately. The profile is who the Shadow represents — stable,
one row, edited rarely. Memory is what has been learned since — many rows, each
independently created, retired or expired. Collapsing them into one document
would make "add a commitment" a rewrite of the whole profile.

Neither is evidence. Retrieved document chunks answer questions about what
SunRadia's documents say; these answer who is asking and what they care about,
and the prompt keeps that line drawn.

Both belong to a `User`. The company knowledge base does not: documents and
chunks stay shared, and only the twin is private. That asymmetry is the whole
multi-user design — one corpus, many people reading it as themselves.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MemoryType(StrEnum):
    """What kind of thing a memory is.

    The type is not decoration: it decides how the memory is presented to the
    model. A PREFERENCE shapes tone, a DECISION constrains what to recommend,
    and a COMMITMENT is something owed to someone.
    """

    FACT = "fact"
    PREFERENCE = "preference"
    DECISION = "decision"
    COMMITMENT = "commitment"
    CONTEXT = "context"


# SQLAlchemy persists a PEP-435 enum by member *name* unless told otherwise,
# which would write "PREFERENCE" while the migration's CHECK constraint expects
# "preference". `values_callable` makes the stored form the member value —
# the same treatment `DocumentStatus` gets, for the same reason.
MemoryTypeType = SAEnum(
    MemoryType,
    name="memory_type",
    native_enum=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

# Lists of short strings — responsibilities, priorities and the like. JSONB on
# Postgres so they are queryable if that is ever needed; plain JSON elsewhere,
# which is what the SQLite test suite creates.
StringListType = JSON().with_variant(JSONB, "postgresql")

MAX_PROFILE_TEXT = 2000


class DigitalTwinProfile(Base):
    """Who the Shadow represents.

    One row per user, enforced by a unique constraint rather than by
    convention: "this user's profile" has to be a fact about the schema, or the
    first bug that writes a second row makes every answer non-deterministic.
    """

    __tablename__ = "digital_twin_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    organization: Mapped[str] = mapped_column(String(255), nullable=False)

    communication_style: Mapped[str | None] = mapped_column(Text, nullable=True)

    responsibilities: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )
    expertise: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )
    priorities: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )
    decision_preferences: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )
    current_focus: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("user_id", name="uq_digital_twin_profile_user"),)


class DigitalTwinMemory(Base):
    """One durable thing worth remembering between conversations.

    Written only through the memory API. Nothing here is extracted
    automatically from chat or from documents — a memory store that fills
    itself is a transcript, and a transcript is not memory.
    """

    __tablename__ = "digital_twin_memory"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[MemoryType] = mapped_column(MemoryTypeType, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 1–5, checked in the schema layer. Higher wins a place in a bounded
    # context before lower.
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="user")

    # Retired rather than deleted by default: a decision that no longer applies
    # is still a thing that was decided.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_digital_twin_memory_user_active",
            "user_id",
            "active",
            "importance",
        ),
    )
