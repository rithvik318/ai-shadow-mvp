"""A report as it was, kept so that it stays as it was.

The weekly work report is a *function* of the current tasks — regenerate it a
month later and you get a month-later answer, because the tasks have moved on.
That is correct for "what does my week look like", and useless for "what did
the last week of August actually say". A digest is worse still: it is derived
from mailbox activity a provider may not retain, may re-page, or may refuse to
serve at all next Tuesday.

So a report that has been generated is written down, whole, and never
recomputed. Three rules follow from that, and each is enforced here rather than
remembered:

**One row per user, type and period.** The unique constraint is the whole
duplicate-prevention story. A scheduler tick that runs twice, or a person
pressing Generate on a period already recorded, finds the existing row.

**A historical row is never overwritten.** Once a period has closed, its
snapshot is final; `report_store` refuses the update rather than the model
forbidding it, because the *provisional* snapshot of a period still running is
legitimately replaceable and the difference is a fact about the clock.

**"Nothing to report" and "could not be produced" are different rows.** A
digest for a week in which no mailbox was connected is recorded with
`status = unavailable` and the reason in words. It is not recorded as an empty
digest, because an empty digest is a claim — that the mailbox was read and held
nothing — and this system did not read one.

`content` is the rendered report as JSON, deliberately denormalised. The point
of a snapshot is that it does not change when the rows it was derived from do,
and a set of foreign keys into `task` would change the moment somebody edited
a task title.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
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

#: JSONB on Postgres so a stored report is queryable if that is ever wanted;
#: plain JSON elsewhere, which is what keeps the SQLite test suite honest.
ReportContentType = JSON().with_variant(JSONB, "postgresql")


class ReportType(StrEnum):
    """The three things this system reports on.

    The work report and the email digests answer different questions and are
    deliberately not one type with a flag: one is about tasks and meetings and
    needs no mailbox, the other two are about correspondence and are impossible
    without one.
    """

    WEEKLY_WORK = "weekly_work"
    WEEKLY_EMAIL_DIGEST = "weekly_email_digest"
    MONTHLY_EMAIL_DIGEST = "monthly_email_digest"


class ReportStatus(StrEnum):
    """Whether the stored content is a report or an explanation of its absence."""

    #: Produced from real data. `content` is the report.
    COMPLETE = "complete"
    #: Could not be produced — no mailbox connected, provider refused. `detail`
    #: says why, in words, and `content` carries no invented figures.
    UNAVAILABLE = "unavailable"


ReportTypeColumn = SAEnum(
    ReportType,
    name="report_type",
    native_enum=False,
    values_callable=lambda enum: [member.value for member in enum],
)

ReportStatusColumn = SAEnum(
    ReportStatus,
    name="report_status",
    native_enum=False,
    values_callable=lambda enum: [member.value for member in enum],
)


class GeneratedReport(Base):
    """One report, for one person, covering one period, as it read then."""

    __tablename__ = "generated_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    report_type: Mapped[ReportType] = mapped_column(ReportTypeColumn, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        ReportStatusColumn, nullable=False, default=ReportStatus.COMPLETE
    )

    # The window, half-open: `period_start <= t < period_end`. Stored rather
    # than derived from a key so that a reader of the table alone can tell what
    # a row covers without knowing this application's calendar rules.
    period_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    #: When the snapshot was taken — not when the period ended. A report of
    #: August generated in October says so.
    generated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )

    #: True while the period is still running. A provisional snapshot may be
    #: replaced; a final one may not. Kept as stored data rather than derived
    #: from `period_end < now` because it records what was true *at generation*
    #: — a row written mid-week stays marked provisional even after the week
    #: closes, which is exactly the signal that it should be regenerated.
    is_provisional: Mapped[bool] = mapped_column(
        # Matches `users.is_admin`: the SQL literal `false`, never
        # `func.false()`, which renders as a call PostgreSQL rejects.
        nullable=False,
        default=False,
        server_default=false(),
    )

    #: Why an `unavailable` report is unavailable, written for a person. Never
    #: carries a token, an address or a stack trace.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    content: Mapped[dict] = mapped_column(
        ReportContentType, nullable=False, default=dict
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "report_type",
            "period_start",
            name="uq_generated_report_period",
        ),
        # The history list's access pattern: one person's reports of one kind,
        # newest period first.
        Index(
            "ix_generated_report_user_type_period",
            "user_id",
            "report_type",
            "period_start",
        ),
    )
