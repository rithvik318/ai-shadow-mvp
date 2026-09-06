"""Producing an email digest for a period, and writing it down.

The one place that turns "generate the August digest for this person" into a
stored row. Both callers go through it — the endpoint a person presses and the
scheduler that runs unattended — so there is exactly one answer to what a
digest for a given period contains, and no second implementation to drift.

Three behaviours are worth stating because each is the opposite of the
convenient default:

**A closed period is read back, never rebuilt.** If the store already holds a
final digest for the window, this returns it without touching the provider. A
digest is a claim about mail that has already happened, and rebuilding it a
month later would produce a *different* claim about the same past — the
provider may have aged messages out, and triage may have run since.

**No mailbox produces a recorded refusal, not an empty digest.** The row goes
in with `status = unavailable` and the reason in words. That is a different
thing from a quiet week, and the difference is preserved all the way to the UI.

**The provider is never called for a period that has not started.** Asking for
next month returns an empty provisional answer rather than a page of this
month's mail filtered to nothing — the same result, but arrived at honestly and
without a round trip.

Serialisation lives here, as plain dictionaries, rather than in the schema
layer: a stored snapshot has to outlive the response model it was written
beside, and a service that imported `app.schemas` would invert the dependency
this codebase keeps between the API edge and the domain.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmailProviderAuthError,
    EmailProviderNotConfiguredError,
)
from app.models.report import GeneratedReport, ReportStatus, ReportType
from app.services.features.reports import (
    activity_report,
    digest_service,
    report_store,
)
from app.services.features.reports.digest_service import DigestMessage, EmailDigest
from app.services.features.reports.period import Period, PeriodKind, previous
from app.services.features.users import user_service

logger = logging.getLogger(__name__)

DIGEST_TYPES = (ReportType.WEEKLY_EMAIL_DIGEST, ReportType.MONTHLY_EMAIL_DIGEST)

NOT_STARTED_DETAIL = "This period has not begun, so there is nothing to report on yet."


@dataclass(frozen=True)
class GenerationResult:
    """A digest, and where it came from."""

    report_type: ReportType
    period: Period
    status: ReportStatus
    generated_at: datetime
    is_provisional: bool
    content: dict
    detail: str | None = None
    #: True when this was read back from the store rather than rebuilt.
    from_history: bool = False


def _message(item: DigestMessage) -> dict:
    return {
        "message_id": item.message_id,
        "subject": item.subject,
        "sender_name": item.sender_name,
        "sender_address": item.sender_address,
        "received_at": item.received_at.isoformat() if item.received_at else None,
        "category": item.category,
        "priority": item.priority,
        "summary": item.summary,
        "needs_reply": item.needs_reply,
    }


def serialise(digest: EmailDigest) -> dict:
    """The digest as it is stored and as it is returned — one shape, always."""

    return {
        "mailbox": digest.mailbox,
        "received_count": digest.received_count,
        "sent_count": digest.sent_count,
        "triaged_count": digest.triaged_count,
        "untriaged_count": digest.untriaged_count,
        "needs_reply_count": digest.needs_reply_count,
        "follow_up_count": digest.follow_up_count,
        "undated_count": digest.undated_count,
        "by_category": dict(digest.by_category),
        "by_priority": dict(digest.by_priority),
        "top_correspondents": [
            {
                "address": person.address,
                "name": person.name,
                "message_count": person.message_count,
            }
            for person in digest.top_correspondents
        ],
        "needs_reply": [_message(item) for item in digest.needs_reply],
        "highlights": [_message(item) for item in digest.highlights],
        "is_quiet": digest.is_quiet,
        "truncated": digest.truncated,
        "truncation_detail": digest.truncation_detail,
    }


def _with_activity(
    db: Session,
    *,
    user_id: uuid.UUID,
    period: Period,
    digest: EmailDigest | None,
    now: datetime,
) -> dict:
    """The stored content: the digest's figures, plus the report a person reads.

    Both in one document rather than two report types. They are two views of
    one period — the counts and the prose — and splitting them would double the
    rows, the scheduling and the history for no gain. `digest` is None when no
    mailbox could be read, and the activity half says so.
    """

    user = user_service.find_user(db, user_id)

    report = activity_report.build(
        db,
        user_id=user_id,
        user_name=user.name if user else "",
        period=period,
        digest=digest,
        now=now,
    )

    content = serialise(digest) if digest is not None else {}
    content["activity"] = activity_report.serialise(report)

    return content


def _from_row(row: GeneratedReport, period: Period) -> GenerationResult:
    return GenerationResult(
        report_type=row.report_type,
        period=period,
        status=row.status,
        generated_at=row.generated_at,
        is_provisional=row.is_provisional,
        content=dict(row.content or {}),
        detail=row.detail,
        from_history=True,
    )


def generate_digest(
    db: Session,
    *,
    user_id: uuid.UUID,
    report_type: ReportType,
    period: Period,
    now: datetime | None = None,
    refresh: bool = False,
) -> GenerationResult:
    """The digest for one person and one period, from the store or from mail.

    `refresh` re-reads the mailbox for a period that is still running. It has
    no effect on a closed one: a final report is final, and a flag that could
    rewrite history would make the guarantee decorative.
    """

    if report_type not in DIGEST_TYPES:
        raise ValueError(f"{report_type} is not an email digest.")

    moment = now or datetime.now(UTC)
    stored = report_store.find(
        db, user_id=user_id, report_type=report_type, period=period
    )

    # A stored *report* for a closed period is final and is served as it was.
    # A stored refusal is not: it says a mailbox could not be read, and the
    # usual next thing that happens is somebody connects one. Retrying it is
    # how last week stops being permanently blank after a mailbox arrives.
    retryable = stored is not None and stored.status is ReportStatus.UNAVAILABLE

    if stored is not None and not stored.is_provisional and not retryable:
        return _from_row(stored, period)

    if stored is not None and not refresh and not retryable:
        return _from_row(stored, period)

    if period.start > moment:
        # Nothing has happened in this window yet. Say so rather than reading a
        # mailbox to prove that the future is empty.
        return GenerationResult(
            report_type=report_type,
            period=period,
            status=ReportStatus.UNAVAILABLE,
            generated_at=moment,
            is_provisional=True,
            content={},
            detail=NOT_STARTED_DETAIL,
        )

    try:
        digest = digest_service.build(db, user_id=user_id, period=period, now=moment)
    except (EmailProviderNotConfiguredError, EmailProviderAuthError) as refusal:
        # No mailbox, but the task half of the period is real and does not
        # depend on email. So the activity report is still produced — saying
        # plainly that email was unavailable — rather than the whole period
        # being recorded as nothing. A person with tasks and no mailbox should
        # still get a report about their week.
        content = _with_activity(
            db, user_id=user_id, period=period, digest=None, now=moment
        )

        row = report_store.record(
            db,
            user_id=user_id,
            report_type=report_type,
            period=period,
            content=content,
            status=ReportStatus.UNAVAILABLE,
            detail=str(refusal),
            now=moment,
        )

        logger.info(
            "digest_unavailable",
            extra={
                "user_id": str(user_id),
                "report_type": str(report_type),
                "period": period.key,
            },
        )

        return _from_row(row, period)

    row = report_store.record(
        db,
        user_id=user_id,
        report_type=report_type,
        period=period,
        content=_with_activity(
            db, user_id=user_id, period=period, digest=digest, now=moment
        ),
        status=ReportStatus.COMPLETE,
        now=moment,
    )

    return GenerationResult(
        report_type=report_type,
        period=period,
        status=row.status,
        generated_at=row.generated_at,
        is_provisional=row.is_provisional,
        content=dict(row.content or {}),
        detail=row.detail,
        from_history=False,
    )


def generate_due_digests(
    db: Session,
    *,
    user_ids: list[uuid.UUID],
    now: datetime | None = None,
) -> list[GenerationResult]:
    """Snapshot the most recently completed week and month for each user.

    What the scheduler calls. Idempotent by construction: every result after
    the first for a given period is read straight back out of the store, so a
    tick every hour costs one query per user per type and no provider call.

    One user's failure does not stop the rest. A digest that could not be built
    is already recorded as `unavailable` by `generate_digest`; anything else
    escaping is logged against the user it belongs to and the loop continues,
    because a scheduled job that abandons forty people over one bad mailbox is
    worse than one that reports forty-one outcomes.
    """

    moment = now or datetime.now(UTC)
    results: list[GenerationResult] = []

    windows = {
        ReportType.WEEKLY_EMAIL_DIGEST: previous(PeriodKind.WEEK, moment),
        ReportType.MONTHLY_EMAIL_DIGEST: previous(PeriodKind.MONTH, moment),
    }

    for user_id in user_ids:
        for report_type, period in windows.items():
            try:
                results.append(
                    generate_digest(
                        db,
                        user_id=user_id,
                        report_type=report_type,
                        period=period,
                        now=moment,
                    )
                )
            except Exception:  # noqa: BLE001 - one user must not stop the rest
                db.rollback()
                logger.exception(
                    "scheduled_digest_failed",
                    extra={
                        "user_id": str(user_id),
                        "report_type": str(report_type),
                        "period": period.key,
                    },
                )

    return results
