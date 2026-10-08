"""A comprehensive activity report, kept so it is produced once.

Separate from `generated_report`, and the separation is the design rather than
an oversight. That table holds *snapshots of a calendar period* — a weekly work
report, a weekly or monthly digest — under a rule stated in its own docstring:
a recorded report is immutable, and the only states a row can be in are "this
is the report" and "this could not be produced". This table holds something
with a different shape and a different life:

**An arbitrary window, not a calendar period.** `GET /reports/activity` takes
two dates. "1–13 September" is a legitimate request and is not any week or any
month, so the identity of a row here is the pair of instants, not a period key.

**A version.** The document is the output of a prompt, a composition and a
quality gate, all of which change. A stored report therefore records the
version of the machinery that produced it, and a version bump makes every older
row simply not match — no deletion, no migration, no report silently mixing
last month's composition with this month's.

**A lifecycle, including "being generated right now".** A digest is written
once it exists. This report takes a mailbox read and a dozen model calls, so
two requests arriving together must not both start one — which means a row has
to exist *before* the work does, saying that somebody is doing it. Putting a
`generating` state into `report_status` would weaken the guarantee the digest
table is built to make, that a row is either the report or an explanation.

**A fingerprint of the evidence.** `evidence_fingerprint` identifies the
correspondence the report was built from. For a period still running it is what
lets a stale-by-clock report be revalidated with a mailbox read and *no* model
calls: same messages, same report, and the stored document stands.

`content` is the whole response payload as JSON, denormalised on purpose — the
same reasoning as `generated_report.content`. It does not store raw message
bodies, but the rendered document it holds **does** contain correspondence-
derived personal data: thread subjects, counterparty names, provider message
ids cited as evidence, the mailbox address, and model-written summaries of
what messages said. Treat this table as holding mail-derived data for access
control, retention and deletion.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UtcDateTime

ActivityContentType = JSON().with_variant(JSONB, "postgresql")


class ActivityReportStatus(StrEnum):
    """Where one report is in its life.

    `GENERATING` is a claim on the work, not a report: it exists so a second
    request for the same window finds it and waits rather than starting its own
    pipeline. `FAILED` is equally deliberate — a generation that raised must
    leave evidence that it raised, never a row that reads as a finished report.
    """

    GENERATING = "generating"
    COMPLETE = "complete"
    FAILED = "failed"


ActivityReportStatusColumn = SAEnum(
    ActivityReportStatus,
    name="activity_report_status",
    native_enum=False,
    # Sized for the longest member with room to spare, because a `VARCHAR(n)`
    # rendered to today's longest value is how migrations 0013 and 0015 came
    # to exist.
    length=32,
    values_callable=lambda enum: [member.value for member in enum],
)


class ActivityReport(Base):
    """One comprehensive activity report, for one person and one window."""

    __tablename__ = "activity_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: The window, exactly as asked for: `period_start <= t < period_end`.
    period_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    #: The version of the report machinery that produced this row. Part of the
    #: identity, so raising it retires every older row without touching one.
    report_version: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[ActivityReportStatus] = mapped_column(
        ActivityReportStatusColumn,
        nullable=False,
        default=ActivityReportStatus.GENERATING,
    )

    #: When this generation was claimed. A `generating` row older than the lock
    #: timeout is an abandoned one — a process died mid-report — and may be
    #: taken over.
    started_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )

    #: When the stored document was produced. Shown to the reader, because a
    #: report reused from storage is only as current as the moment it was made.
    generated_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: When the evidence behind it was last confirmed current. Equal to
    #: `generated_at` for a fresh report, and later than it for one that was
    #: revalidated against an unchanged mailbox.
    checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: The instant the evidence runs to: the last message read, or the end of
    #: the window. This is what "data through" means on screen, and it is not
    #: the same as `generated_at`.
    data_through: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: True when the window was still open at generation. Such a row may be
    #: replaced; one for a closed window may not.
    is_provisional: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )

    #: Identifies the correspondence this was built from — counts and message
    #: ids hashed, never their contents.
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Why a `failed` row failed, in words. Never a token, an address or a
    #: stack trace.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    content: Mapped[dict] = mapped_column(
        ActivityContentType, nullable=False, default=dict
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "period_start",
            "period_end",
            "report_version",
            name="uq_activity_report_window",
        ),
        Index(
            "ix_activity_report_user_window",
            "user_id",
            "period_start",
            "period_end",
        ),
    )
