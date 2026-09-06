"""A meeting, and what is known about whether the person went to it.

One table, owned the private way: `user_id` is a foreign key into `users`, the
same ownership tasks and the Digital Twin use, not the shared string the
knowledge base uses. Two people invited to one meeting have two rows, because
attendance is a fact about a person and not about a meeting.

**Attendance is recorded, never assumed.** `status` starts as `scheduled` and
only becomes `attended` or `missed` when somebody says so or when evidence is
recorded — `attendance_evidence` says which. A meeting that has finished with
nothing recorded stays `scheduled` here and reads as `unknown`, which is
computed in `services/features/calendar/attendance.py` rather than swept in by
a background job. The row keeps what was recorded; the read says what is known.

**`source` distinguishes a meeting a person typed in from one a provider
supplied.** Calendar consent has not been granted to the Graph application, so
every row today is `manual`. When consent arrives, `provider_event_id` is the
identity that stops a re-import creating a second copy — the same role
`source_key` plays for tasks.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UtcDateTime
from app.services.features.calendar.attendance import AttendanceEvidence, EventStatus

# varchar + CHECK rather than a native PG enum, matching the rest of the
# schema: adding a value to a native enum is a migration with a lock.
EventStatusType = SAEnum(
    EventStatus,
    name="event_status",
    native_enum=False,
    values_callable=lambda enum: [member.value for member in enum],
)

AttendanceEvidenceType = SAEnum(
    AttendanceEvidence,
    name="attendance_evidence",
    native_enum=False,
    values_callable=lambda enum: [member.value for member in enum],
)


class CalendarEvent(Base):
    """One meeting in one person's week."""

    __tablename__ = "calendar_event"

    __table_args__ = (
        # One row per provider event per person. Nullable, so manually created
        # events do not collide — several may legitimately have no provider id.
        UniqueConstraint("user_id", "provider_event_id", name="uq_event_provider_id"),
        Index("ix_calendar_event_user_starts", "user_id", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        # Cascade: an event is meaningless without the person whose week it is,
        # and deleting a user must not leave orphan rows behind.
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)

    starts_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    # Nullable: not every calendar entry has a stated end, and inventing one
    # would make an instantaneous item look like a meeting of some length.
    ends_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    status: Mapped[EventStatus] = mapped_column(
        EventStatusType, nullable=False, default=EventStatus.SCHEDULED
    )
    attendance_evidence: Mapped[AttendanceEvidence] = mapped_column(
        AttendanceEvidenceType, nullable=False, default=AttendanceEvidence.NONE
    )
    # When somebody answered, so a stale answer is distinguishable from a fresh
    # one. Null while nobody has.
    attendance_recorded_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    attendance_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured, for the same reason `EmailAssessment` splits its sender: an
    # organiser rendered as "Robert Keenan <r@sunradia.com>" is not an address
    # and must never be used as one.
    organiser_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organiser_address: Mapped[str | None] = mapped_column(String(320), nullable=True)

    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
