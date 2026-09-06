"""Writing a report down once, and refusing to rewrite it afterwards.

The rule this module exists to hold: **a report covering a period that has
closed is never regenerated.** Once the last week of August is on disk, asking
for it again returns what was stored, not a fresh computation over today's
tasks. Anything else makes history a function of when you looked at it.

There is exactly one exception, and it is about the clock rather than about
policy. A report for a period that is *still running* is provisional: the week
is not over, the numbers will change, and asking again should show the current
state. Those rows are marked `is_provisional` and are replaced in place. The
moment the period closes, the next generation writes a final row over the
provisional one and no generation after that touches it.

The unique constraint on `(user_id, report_type, period_start)` is what makes
this safe under a race between the scheduler and a person pressing Generate.
The service checks first because that gives a useful answer in the ordinary
case; the constraint is what happens when two ticks arrive at once, and the
integrity error is caught and resolved by re-reading rather than by retrying
the generation.

`content` is stored as plain JSON built by the serialisers here rather than by
the API schema layer, deliberately: a stored snapshot must not change shape
when a response model gains a field, and a schema that rendered from the
database would be rendering a different thing from one that rendered from a
freshly built report.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.report import GeneratedReport, ReportStatus, ReportType
from app.services.features.reports.period import Period, PeriodKind

logger = logging.getLogger(__name__)

#: Which period rhythm each report type is generated on. Stated once, here, so
#: a new type cannot be added without deciding it.
PERIOD_KIND: dict[ReportType, PeriodKind] = {
    ReportType.WEEKLY_WORK: PeriodKind.WEEK,
    ReportType.WEEKLY_EMAIL_DIGEST: PeriodKind.WEEK,
    ReportType.MONTHLY_EMAIL_DIGEST: PeriodKind.MONTH,
}


def period_kind(report_type: ReportType) -> PeriodKind:
    return PERIOD_KIND[report_type]


def find(
    db: Session,
    *,
    user_id: uuid.UUID,
    report_type: ReportType,
    period: Period,
) -> GeneratedReport | None:
    """The stored report for this user, type and period, or None."""

    return db.execute(
        select(GeneratedReport).where(
            GeneratedReport.user_id == user_id,
            GeneratedReport.report_type == report_type,
            GeneratedReport.period_start == period.start,
        )
    ).scalar_one_or_none()


def history(
    db: Session,
    *,
    user_id: uuid.UUID,
    report_type: ReportType | None = None,
    limit: int = 50,
) -> list[GeneratedReport]:
    """This user's stored reports, newest period first.

    Filtered on `user_id` unconditionally. There is no call shape here that
    could return another person's report, which is the same guarantee every
    other user-scoped service in this codebase makes by construction rather
    than by review.
    """

    query = select(GeneratedReport).where(GeneratedReport.user_id == user_id)

    if report_type is not None:
        query = query.where(GeneratedReport.report_type == report_type)

    query = query.order_by(
        GeneratedReport.period_start.desc(), GeneratedReport.report_type
    ).limit(max(1, min(limit, 200)))

    return list(db.execute(query).scalars().all())


def is_final(period: Period, now: datetime | None = None) -> bool:
    """Whether this period has closed and its report may be written once."""

    return period.end <= (now or datetime.now(UTC))


def record(
    db: Session,
    *,
    user_id: uuid.UUID,
    report_type: ReportType,
    period: Period,
    content: dict,
    status: ReportStatus = ReportStatus.COMPLETE,
    detail: str | None = None,
    now: datetime | None = None,
) -> GeneratedReport:
    """Store this report, or return the final one already stored.

    Never raises on a duplicate. A caller that has just spent a provider round
    trip building a digest for a period somebody else recorded a second earlier
    should get that row, not an error — the two are the same report by
    construction.
    """

    moment = now or datetime.now(UTC)
    provisional = not is_final(period, moment)

    existing = find(db, user_id=user_id, report_type=report_type, period=period)

    if existing is not None:
        if not existing.is_provisional and existing.status is ReportStatus.COMPLETE:
            # Final, and it is a report. This is the whole guarantee.
            return existing

        if not existing.is_provisional and status is ReportStatus.UNAVAILABLE:
            # Still could not be produced. Nothing to replace it with, and
            # rewriting the timestamp would suggest something was retried
            # successfully.
            return existing

        # An `unavailable` row for a closed period is *not* immutable, and the
        # difference matters more than it looks. It records that nothing could
        # be read — most often because no mailbox was connected yet — and the
        # ordinary sequence is that somebody connects one and asks again. If a
        # recorded failure were final, connecting a mailbox on Tuesday would
        # leave last week permanently blank with no way to fix it. A recorded
        # *report* stays immutable; a recorded refusal is a placeholder.

        _apply(
            existing,
            content=content,
            status=status,
            detail=detail,
            moment=moment,
            provisional=provisional,
        )
        db.commit()
        db.refresh(existing)

        return existing

    row = GeneratedReport(
        user_id=user_id,
        report_type=report_type,
        period_start=period.start,
        period_end=period.end,
    )
    _apply(
        row,
        content=content,
        status=status,
        detail=detail,
        moment=moment,
        provisional=provisional,
    )
    db.add(row)

    try:
        db.commit()
    except IntegrityError:
        # Two writers raced on the unique constraint. The other one won, and
        # its row says the same thing this one would have.
        db.rollback()
        found = find(db, user_id=user_id, report_type=report_type, period=period)

        if found is None:  # pragma: no cover - the constraint is the only cause
            raise

        return found

    db.refresh(row)

    logger.info(
        "report_recorded",
        extra={
            "user_id": str(user_id),
            "report_type": str(report_type),
            "period": period.key,
            "status": str(status),
            "provisional": provisional,
        },
    )

    return row


def _apply(
    row: GeneratedReport,
    *,
    content: dict,
    status: ReportStatus,
    detail: str | None,
    moment: datetime,
    provisional: bool,
) -> None:
    row.status = status
    row.detail = detail
    row.content = content
    row.generated_at = moment
    row.is_provisional = provisional
