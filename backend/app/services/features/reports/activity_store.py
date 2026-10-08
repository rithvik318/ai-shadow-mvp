"""Where a comprehensive activity report is kept, and when it is believed.

Every decision about reuse lives here. The service above it fetches and
generates; this module answers three questions and nothing else: is there a
report for this window, may it be served, and may I start building one?

**A closed window is answered from storage, always.** A report of 7–13
September cannot change after the 13th, so the second request for it never
reads a mailbox and never calls a model. That is the whole point of the table.

**An open window is answered from storage while it is fresh.** The current week
does change, so a stored report for it carries an age. Inside
`fresh_for`, it is served as it stands. Outside it, the service re-reads the
mailbox — cheap — and compares `evidence_fingerprint`: unchanged correspondence
means the same report, so the row is touched and served without a single model
call. Only genuinely new messages cause a regeneration.

**One generation at a time.** A row exists before the work does, in state
`generating`, and it is the unique constraint on `(user, window, version)` that
makes claiming it atomic. A second request finds the claim and is told the
report is being built rather than starting a second pipeline. A claim older
than `lock_expires_after` is treated as abandoned — a process died holding it —
and may be taken over, because the alternative is a window that can never be
generated again.

`report_version` is part of the identity rather than a column to check, so a
version bump does not invalidate anything: it simply stops matching, and the
next request generates under the new version beside the old row.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.activity_report import ActivityReport, ActivityReportStatus
from app.services.features.reports.correspondence import Correspondence
from app.services.features.reports.period import Period

logger = logging.getLogger(__name__)

#: The version of the report machinery. Raise it when a change to the prompts,
#: the analysis, the composition or the response shape means a stored report is
#: no longer the report this code would produce. Older rows stop matching and
#: are left alone; nothing is deleted and nothing is served under the wrong
#: version.
#:
#: 1 — the first stored version (pipeline of 2026-09-15: sent folder read,
#:     single-pass thread analysis, budgeted model calls).
REPORT_VERSION = "1"


class ReportBeingGenerated(Exception):
    """Somebody else is already building this exact report.

    Not an error condition: it is the correct answer to a second simultaneous
    request, and the API turns it into `202 Accepted` with the claim's age.
    """

    def __init__(self, row: ActivityReport) -> None:
        super().__init__("This report is already being generated.")
        self.row = row


@dataclass(frozen=True)
class Policy:
    """How long a stored report is believed.

    Held as data rather than read from settings inside the functions, so the
    rules can be exercised without a configuration fixture and so a caller
    cannot half-apply them.
    """

    #: How long a report for a window that is still open is served unchecked.
    fresh_for: timedelta
    #: After that: re-read the mailbox and compare fingerprints rather than
    #: regenerating blindly.
    lock_expires_after: timedelta

    @classmethod
    def from_settings(cls, settings) -> Policy:
        return cls(
            fresh_for=timedelta(seconds=settings.ACTIVITY_REPORT_FRESH_SECONDS),
            lock_expires_after=timedelta(seconds=settings.ACTIVITY_REPORT_LOCK_SECONDS),
        )


def fingerprint(correspondence: Correspondence) -> str:
    """Identify a body of correspondence without storing any of it.

    Message ids, their count, and the latest timestamp — hashed. Two fetches
    that produce the same fingerprint produce the same report, which is what
    lets a stale-by-clock report be revalidated without asking a model
    anything. Ids rather than contents because this value is stored, and a
    stored report deliberately holds no correspondence.
    """

    ids = sorted(message.message_id for message in correspondence.messages)
    latest = max(
        (m.occurred_at for m in correspondence.messages if m.occurred_at),
        default=None,
    )
    material = "\n".join([str(len(ids)), latest.isoformat() if latest else "-", *ids])

    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def data_through(correspondence: Correspondence, *, now: datetime) -> datetime:
    """The instant the evidence runs to.

    The last message read, or the end of the window when that is already past.
    Not `generated_at`: a report generated on Monday about last week has
    evidence running to the end of last week, and saying otherwise would invite
    a reader to think it covers the weekend.
    """

    latest = max(
        (m.occurred_at for m in correspondence.messages if m.occurred_at),
        default=None,
    )
    horizon = min(correspondence.period.end, now)

    return max(latest, horizon) if latest else horizon


def find(
    db: Session,
    *,
    user_id: uuid.UUID,
    period: Period,
    version: str | None = None,
) -> ActivityReport | None:
    """The row for exactly this user, window and version, or None.

    `version` defaults inside the body rather than in the signature so that it
    is read when the call happens. A default evaluated at import time is a
    version that cannot be changed without restarting the process, which would
    make the one mechanism intended to retire stored reports the hardest thing
    in the module to exercise.
    """

    version = version or REPORT_VERSION

    return db.execute(
        select(ActivityReport).where(
            ActivityReport.user_id == user_id,
            ActivityReport.period_start == period.start,
            ActivityReport.period_end == period.end,
            ActivityReport.report_version == version,
        )
    ).scalar_one_or_none()


def is_closed(period: Period, *, now: datetime) -> bool:
    """Whether the window has ended and can no longer gain messages.

    Strictly before `now`, and the strictness is the whole point. A
    month-to-date request builds a window ending at this instant, so `end <=
    now` would call the current month closed and cache it for ever — which is
    the opposite of what a report of a period still running needs.
    """

    return period.end < now


def is_fresh(row: ActivityReport, *, policy: Policy, now: datetime) -> bool:
    """Whether an open window's stored report may be served unchecked."""

    checked = row.checked_at or row.generated_at

    return checked is not None and (now - checked) < policy.fresh_for


def servable(
    row: ActivityReport | None,
    *,
    period: Period,
    policy: Policy,
    now: datetime,
) -> ActivityReport | None:
    """The stored report if it may be served as it stands, else None.

    A closed window's complete report is always servable — that is the rule
    this table exists for. An open window's is servable while it is fresh.
    Anything else (missing, failed, generating, stale) is not, and the caller
    decides what to do about it.
    """

    if row is None or row.status is not ActivityReportStatus.COMPLETE:
        return None

    if is_closed(period, now=now):
        return row

    return row if is_fresh(row, policy=policy, now=now) else None


def claim(
    db: Session,
    *,
    user_id: uuid.UUID,
    period: Period,
    policy: Policy,
    now: datetime,
    version: str | None = None,
) -> ActivityReport:
    """Take the right to generate this report, or raise `ReportBeingGenerated`.

    Committed before it returns, deliberately: the claim is only worth
    something if another request's transaction can see it, and the work it
    guards takes minutes.
    """

    version = version or REPORT_VERSION
    existing = find(db, user_id=user_id, period=period, version=version)

    if existing is None:
        row = ActivityReport(
            user_id=user_id,
            period_start=period.start,
            period_end=period.end,
            report_version=version,
            status=ActivityReportStatus.GENERATING,
            started_at=now,
            is_provisional=not is_closed(period, now=now),
            content={},
        )
        db.add(row)

        try:
            db.commit()
        except IntegrityError:
            # Another request inserted the same window between the lookup and
            # the insert. Theirs is the claim.
            db.rollback()
            found = find(db, user_id=user_id, period=period, version=version)

            if found is None:  # pragma: no cover - the constraint is the cause
                raise

            raise ReportBeingGenerated(found) from None

        db.refresh(row)

        return row

    if (
        existing.status is ActivityReportStatus.GENERATING
        and (now - existing.started_at) < policy.lock_expires_after
    ):
        raise ReportBeingGenerated(existing)

    # Free to take: complete-but-stale, failed, or a claim whose holder died.
    # The guarded UPDATE is what makes taking it atomic — two requests reaching
    # here together, only one row moves to `generating` with this `started_at`.
    taken = db.execute(
        update(ActivityReport)
        .where(
            ActivityReport.id == existing.id,
            ActivityReport.started_at == existing.started_at,
        )
        .values(status=ActivityReportStatus.GENERATING, started_at=now, detail=None)
    )
    db.commit()

    if taken.rowcount != 1:
        db.refresh(existing)

        raise ReportBeingGenerated(existing)

    db.refresh(existing)

    return existing


def complete(
    db: Session,
    row: ActivityReport,
    *,
    content: dict,
    correspondence: Correspondence,
    period: Period,
    now: datetime,
) -> ActivityReport:
    """Record a finished report against the claim that produced it."""

    row.status = ActivityReportStatus.COMPLETE
    row.content = content
    row.generated_at = now
    row.checked_at = now
    row.data_through = data_through(correspondence, now=now)
    row.evidence_fingerprint = fingerprint(correspondence)
    row.is_provisional = not is_closed(period, now=now)
    row.detail = None

    db.commit()
    db.refresh(row)

    logger.info(
        "activity_report_stored user_id=%s period=%s..%s version=%s "
        "provisional=%s words=%s",
        row.user_id,
        period.start.date(),
        period.end.date(),
        row.report_version,
        row.is_provisional,
        content.get("word_count"),
    )

    return row


def revalidate(db: Session, row: ActivityReport, *, now: datetime) -> ActivityReport:
    """The evidence is unchanged, so the stored report still stands.

    Moves `checked_at` and nothing else. `generated_at` stays where it was,
    because the document was not rebuilt and saying otherwise would tell a
    reader the report is newer than it is.
    """

    row.status = ActivityReportStatus.COMPLETE
    row.checked_at = now
    db.commit()
    db.refresh(row)

    return row


def fail(
    db: Session, row: ActivityReport, *, detail: str, now: datetime
) -> ActivityReport:
    """Record that this generation raised.

    A failed generation must never leave a row that reads as a report. It
    leaves this instead: the claim released, the reason kept, and the window
    free for the next attempt.
    """

    row.status = ActivityReportStatus.FAILED
    row.detail = detail[:500]
    row.started_at = now
    db.commit()
    db.refresh(row)

    logger.warning(
        "activity_report_failed user_id=%s period=%s..%s error=%s",
        row.user_id,
        row.period_start.date(),
        row.period_end.date(),
        row.detail,
    )

    return row


def history(
    db: Session, *, user_id: uuid.UUID, limit: int = 50
) -> list[ActivityReport]:
    """This user's stored activity reports, newest window first."""

    return list(
        db.execute(
            select(ActivityReport)
            .where(ActivityReport.user_id == user_id)
            .order_by(ActivityReport.period_start.desc())
            .limit(max(1, min(limit, 200)))
        )
        .scalars()
        .all()
    )


def now_utc() -> datetime:
    return datetime.now(UTC)
