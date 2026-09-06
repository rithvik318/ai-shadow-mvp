"""Reports: the weekly work report and the two email digests, with history.

Every route resolves its owner from `CurrentUser`. There is no path or query
parameter here that names a user, so there is no shape of request that reads
somebody else's report — the same guarantee the tasks and email routes make,
made the same way.

The three types are one endpoint rather than three because a client picking
between them is picking a value, not a URL, and because the history list and
the period selector are identical for all three. What differs is what a type
*needs*: the work report needs tasks and no mailbox, and the digests need a
mailbox and refuse honestly without one.

The weekly work report is snapshotted here rather than in the service layer,
because rendering it needs the response models and this codebase keeps schemas
out of services. The digests are snapshotted in `generation_service`, because
the scheduler generates those unattended and must not depend on the API.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.api.renderers import weekly_report_response
from app.core.exceptions import ReportPeriodError
from app.database.session import get_db
from app.models.report import GeneratedReport, ReportStatus, ReportType
from app.schemas.document_schema import ErrorResponse
from app.schemas.report_schema import (
    PeriodResponse,
    ReportEnvelope,
    ReportHistoryResponse,
    ReportSummaryResponse,
)
from app.services.features.reports import (
    document_service,
    generation_service,
    report_store,
    weekly_report_service,
)
from app.services.features.reports.period import (
    Period,
    current,
    parse_key,
    preceding,
)

router = APIRouter(prefix="/reports", tags=["reports"])

#: How many *completed* periods the selector offers alongside the one in
#: progress. One, deliberately.
#:
#: The arithmetic can name any week back to the epoch, and an earlier version
#: of this offered twelve — which put eleven guaranteed-empty options in a
#: dropdown and made "previous week" hard to find among them. This system has
#: no history before it was deployed, so the periods worth offering are the one
#: running, the one just finished, and any period somebody has actually
#: generated a report for. Those last are added from the store below.
SELECTOR_DEPTH = 1

#: How many stored reports the history list returns. Five is a month of weeks
#: or five months, which is as far back as anybody looks in an MVP — and a list
#: that grows without bound turns the Reports page into an archive index.
HISTORY_LIMIT = 5

#: What each report type is called to a person. The stored enum values are
#: historical — the two digests grew into the activity reports without a data
#: migration, because renaming a value that sits in every stored row buys a
#: tidier database and risks the history it holds.
REPORT_TITLES: dict[ReportType, str] = {
    ReportType.WEEKLY_WORK: "Weekly Work Report",
    ReportType.WEEKLY_EMAIL_DIGEST: "Weekly Activity Report",
    ReportType.MONTHLY_EMAIL_DIGEST: "Monthly Activity Report",
}

_BAD_PERIOD = {
    400: {"model": ErrorResponse, "description": "The period key is not valid"}
}


def _period_response(period: Period, *, now: datetime) -> PeriodResponse:
    return PeriodResponse(
        kind=period.kind,
        key=period.key,
        label=period.label,
        start=period.start,
        end=period.end,
        is_complete=period.end <= now,
    )


def _resolve_period(report_type: ReportType, key: str | None) -> Period:
    """The window a request means, or refuse it.

    A malformed key is a 400 and never a silent fall back to the current
    period: showing somebody a different week from the one they asked for is
    the failure mode a report must not have.
    """

    kind = report_store.period_kind(report_type)

    if key is None or not key.strip():
        return current(kind)

    try:
        return parse_key(kind, key)
    except ValueError as bad:
        raise ReportPeriodError(str(bad)) from bad


def _summary(row: GeneratedReport, *, now: datetime) -> ReportSummaryResponse:
    period = Period(
        kind=report_store.period_kind(row.report_type),
        start=row.period_start,
        end=row.period_end,
    )

    return ReportSummaryResponse(
        report_type=row.report_type,
        status=row.status,
        period=_period_response(period, now=now),
        generated_at=row.generated_at,
        is_provisional=row.is_provisional,
        detail=row.detail,
    )


def _weekly_work(
    db: Session,
    *,
    user_id: uuid.UUID,
    period: Period,
    now: datetime,
    refresh: bool,
) -> ReportEnvelope:
    """The work report for a period, read back if that period has closed.

    The live builder answers "what does my work look like *now*", which is the
    right answer for the current week and the wrong one for a past week — the
    tasks have moved on since. So a closed period is served from the store, and
    only the running one is rebuilt.
    """

    stored = report_store.find(
        db, user_id=user_id, report_type=ReportType.WEEKLY_WORK, period=period
    )

    if stored is not None and (not stored.is_provisional or not refresh):
        return ReportEnvelope(
            report_type=ReportType.WEEKLY_WORK,
            status=stored.status,
            period=_period_response(period, now=now),
            generated_at=stored.generated_at,
            is_provisional=stored.is_provisional,
            from_history=True,
            detail=stored.detail,
            content=dict(stored.content or {}),
        )

    report = weekly_report_service.build_weekly_report(
        db,
        user_id=user_id,
        now=now,
        # The selected week, not today's. Without these the builder answers
        # "what does my work look like now" for every period, so August would
        # be shown this morning's tasks under an August heading.
        period_start=period.start,
        period_end=period.end,
    )
    content = weekly_report_response(report).model_dump(mode="json")

    row = report_store.record(
        db,
        user_id=user_id,
        report_type=ReportType.WEEKLY_WORK,
        period=period,
        content=content,
        status=ReportStatus.COMPLETE,
        now=now,
    )

    return ReportEnvelope(
        report_type=ReportType.WEEKLY_WORK,
        status=row.status,
        period=_period_response(period, now=now),
        generated_at=row.generated_at,
        is_provisional=row.is_provisional,
        from_history=False,
        detail=row.detail,
        content=dict(row.content or {}),
    )


@router.get(
    "",
    response_model=ReportEnvelope,
    responses=_BAD_PERIOD,
    summary="One report for the caller: work report or email digest",
)
def get_report(
    user: CurrentUser,
    db: Session = Depends(get_db),
    report_type: ReportType = Query(
        default=ReportType.WEEKLY_WORK,
        description="Which report to produce.",
    ),
    period: str | None = Query(
        default=None,
        description=(
            "The period key — 2026-08-31 for a week (a Monday), 2026-08 for a "
            "month. Omit for the period in progress."
        ),
    ),
    refresh: bool = Query(
        default=False,
        description=(
            "Rebuild a report for a period that is still running. Has no "
            "effect on a closed period, whose report is final."
        ),
    ),
) -> ReportEnvelope:
    """Build or read back one report for the calling user.

    A period that has closed is answered from the store and never recomputed.
    A period still in progress is rebuilt on request, which is why its snapshot
    is marked provisional.
    """

    now = datetime.now(UTC)
    window = _resolve_period(report_type, period)

    if report_type is ReportType.WEEKLY_WORK:
        return _weekly_work(
            db, user_id=user.id, period=window, now=now, refresh=refresh
        )

    result = generation_service.generate_digest(
        db,
        user_id=user.id,
        report_type=report_type,
        period=window,
        now=now,
        refresh=refresh,
    )

    return ReportEnvelope(
        report_type=result.report_type,
        status=result.status,
        period=_period_response(result.period, now=now),
        generated_at=result.generated_at,
        is_provisional=result.is_provisional,
        from_history=result.from_history,
        detail=result.detail,
        content=result.content,
    )


@router.get(
    "/history",
    response_model=ReportHistoryResponse,
    summary="Which reports of one type exist, and which periods can be asked for",
)
def report_history(
    user: CurrentUser,
    db: Session = Depends(get_db),
    report_type: ReportType = Query(default=ReportType.WEEKLY_WORK),
    limit: int = Query(default=HISTORY_LIMIT, ge=1, le=50),
) -> ReportHistoryResponse:
    """This user's stored reports of one type, newest period first.

    `available_periods` is what the calendar offers rather than what happens to
    be stored, so the selector is populated on a person's first ever visit and
    asking for an ungenerated week produces one.
    """

    now = datetime.now(UTC)
    rows = report_store.history(
        db, user_id=user.id, report_type=report_type, limit=limit
    )

    kind = report_store.period_kind(report_type)
    running = current(kind, now)

    # The calendar's answer, plus every period this user actually has a report
    # for. The first two are always offerable — a person can ask for last week
    # before anybody has generated it — and the rest exist because they were
    # generated, so an older report stays reachable without padding the list
    # with empty months nobody wrote.
    offered: dict[str, Period] = {
        window.key: window for window in (running, *preceding(running, SELECTOR_DEPTH))
    }

    for row in rows:
        window = Period(kind=kind, start=row.period_start, end=row.period_end)
        offered.setdefault(window.key, window)

    ordered = sorted(offered.values(), key=lambda window: window.start, reverse=True)

    return ReportHistoryResponse(
        report_type=report_type,
        items=[_summary(row, now=now) for row in rows],
        available_periods=[_period_response(window, now=now) for window in ordered],
        total=len(rows),
    )


@router.post(
    "/digests/run",
    response_model=ReportHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Snapshot the caller's last completed week and month of email",
)
def run_digests(
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ReportHistoryResponse:
    """Generate this user's due digests now, the same way the scheduler does.

    Exists so the scheduled path can be exercised deliberately — by a person
    who has just connected a mailbox, or by an operator checking the job — with
    no code path the schedule does not also take. Scoped to the caller: it can
    only ever generate the caller's own digests.
    """

    now = datetime.now(UTC)
    generation_service.generate_due_digests(db, user_ids=[user.id], now=now)

    rows = report_store.history(db, user_id=user.id, limit=200)

    return ReportHistoryResponse(
        report_type=ReportType.WEEKLY_EMAIL_DIGEST,
        items=[
            _summary(row, now=now)
            for row in rows
            if row.report_type in generation_service.DIGEST_TYPES
        ],
        available_periods=[],
        total=sum(
            1 for row in rows if row.report_type in generation_service.DIGEST_TYPES
        ),
    )


@router.get(
    "/document",
    responses={
        404: {"model": ErrorResponse, "description": "No report for that period"},
        **_BAD_PERIOD,
    },
    summary="Download one stored report as a Word document",
)
def report_document(
    user: CurrentUser,
    db: Session = Depends(get_db),
    report_type: ReportType = Query(default=ReportType.WEEKLY_EMAIL_DIGEST),
    period: str | None = Query(default=None),
) -> Response:
    """The stored report, rendered.

    Reads the **stored** row and never rebuilds, which is what makes a
    downloaded document identical to the one on screen and identical again next
    month. Generating on download would let a report of August change because
    a task was completed in September.

    `404` when nothing has been generated for that period yet. Producing a
    document on the way out would put a report in somebody's downloads folder
    that appears in no history and that nobody chose to generate.
    """

    window = _resolve_period(report_type, period)
    stored = report_store.find(
        db, user_id=user.id, report_type=report_type, period=window
    )

    if stored is None:
        raise ReportPeriodError(
            f"No {REPORT_TITLES[report_type]} has been generated for "
            f"{window.label}. Open it first to generate it."
        )

    title = REPORT_TITLES[report_type]
    body = document_service.render(dict(stored.content or {}), report_title=title)

    return Response(
        content=body,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                "attachment; filename="
                + document_service.filename_for(
                    report_title=title, period_key=window.key
                )
            )
        },
    )
