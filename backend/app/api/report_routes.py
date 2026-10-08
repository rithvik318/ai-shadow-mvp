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

import logging
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.api.renderers import weekly_report_response
from app.config.settings import settings
from app.core.exceptions import EmailProviderNotConfiguredError, ReportPeriodError
from app.database.session import get_db
from app.models.report import GeneratedReport, ReportStatus, ReportType
from app.schemas.document_schema import ErrorResponse
from app.schemas.report_schema import (
    ActivityReportPendingResponse,
    ActivityReportResponse,
    ActivitySource,
    PeriodResponse,
    ReportEnvelope,
    ReportHistoryResponse,
    ReportSummaryResponse,
)
from app.services.features.email import mailbox_config_service
from app.services.features.reports import (
    activity_service,
    document_service,
    generation_service,
    report_store,
    weekly_report_service,
)
from app.services.features.reports.activity_store import Policy, ReportBeingGenerated
from app.services.features.reports.period import (
    Period,
    PeriodKind,
    current,
    parse_key,
    preceding,
    range_between,
    to_date,
)
from app.services.features.tasks import task_service
from app.services.llm.client import llm_configured

logger = logging.getLogger(__name__)

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


def _resolve_activity_period(
    start: date | None, end: date | None, *, now: datetime
) -> Period:
    """Turn two optional dates into the window the report covers.

    Neither given means "this month so far", which is the request the product
    is most often asked for and the one the old period model could not express.
    `end` is a date the caller means inclusively, so it is widened to the end of
    that day — a report for "1 to 13 September" that stopped at midnight on the
    13th would silently omit the day the reader asked about.
    """

    if start is None and end is None:
        return to_date(PeriodKind.MONTH, now)

    first = (
        datetime(start.year, start.month, start.day, tzinfo=UTC)
        if start is not None
        else datetime(now.year, now.month, 1, tzinfo=UTC)
    )
    # An open-ended request runs to the end of *today*, not to this instant.
    # The window is the identity of a stored report, and a window ending at
    # `now` is a different window on every request — which would mean the
    # current week could never be read from storage, because no two requests
    # would ever ask for the same one. Ending on a day boundary makes
    # "September so far" the same question all day; no mail can arrive from
    # the future, so the evidence is identical either way.
    last = (
        datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
        if (end is not None)
        else datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)
    )

    try:
        return range_between(first, last)
    except ValueError as exc:
        raise ReportPeriodError("A reporting period must end after it starts.") from exc


def _mailbox_for(db: Session, *, user_id: uuid.UUID):
    """The provider and address this user's report reads."""

    address = mailbox_config_service.resolve_address(db, user_id=user_id)

    if not address:
        raise EmailProviderNotConfiguredError(
            "No mailbox is connected for this user, so there is no "
            "correspondence to report on. Connect one with PUT /email/mailbox."
        )

    return mailbox_config_service.provider_for(db, user_id=user_id), address


def _task_context(db: Session, *, user_id: uuid.UUID, period: Period) -> str:
    """Tasks as supplementary evidence, said plainly to be supplementary.

    Deliberately a short block rather than the report's backbone: work
    routinely happens without a task record, and a period with no tasks is not
    a period with no work. The wording exists so the analysis stage cannot read
    an empty task list as an empty month.
    """

    tasks = task_service.list_tasks(db, user_id=user_id)
    completed = [task for task in tasks if period.contains(task.completed_at)]
    open_tasks = [task for task in tasks if task.completed_at is None]

    if not tasks:
        return (
            "No tasks are recorded for this user. Tasks are supplementary "
            "evidence only; judge the period by its correspondence."
        )

    lines = [
        "Tasks are supplementary evidence, not the definition of work done.",
        f"{len(completed)} task(s) were completed in this period:",
    ]
    lines += [f"- {task.title}" for task in completed[:20]] or ["- (none)"]
    lines.append(f"{len(open_tasks)} task(s) are open:")
    lines += [f"- {task.title}" for task in open_tasks[:20]] or ["- (none)"]

    return "\n".join(lines)


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


def _activity_response(
    outcome: activity_service.Outcome, *, period: Period, now: datetime
) -> ActivityReportResponse:
    """One response shape, whether the report was stored or built just now.

    The stored payload holds the document and its counts; everything about
    *this* request — which window was asked for, and where the answer came
    from — is added here. A reply from storage and a reply from a fresh
    generation therefore differ in their provenance fields and in nothing else.
    """

    row = outcome.row

    return ActivityReportResponse(
        source=ActivitySource(outcome.source.value),
        report_version=row.report_version,
        is_provisional=row.is_provisional,
        generated_at=row.generated_at or now,
        checked_at=row.checked_at,
        data_through=row.data_through,
        period=_period_response(period, now=now),
        period_label=period.label,
        is_partial=period.is_partial,
        **row.content,
    )


def _pending_response(
    error: ReportBeingGenerated, *, period: Period, now: datetime
) -> JSONResponse:
    """`202 Accepted`: somebody else is building this exact report.

    Not an error. Two people opening the same week at the same moment is
    ordinary, and the answer to the second is "it is being made", not a second
    pipeline run. The claim's age is included so a client can say how long it
    has been going rather than showing an endless spinner.
    """

    body = ActivityReportPendingResponse(
        period=_period_response(period, now=now),
        period_label=period.label,
        started_at=error.row.started_at,
        elapsed_seconds=round((now - error.row.started_at).total_seconds(), 1),
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=jsonable_encoder(body),
    )


def _obtain_activity_report(
    user,
    db: Session,
    *,
    start: date | None,
    end: date | None,
    refresh: bool,
):
    """The shared body of the two activity endpoints.

    `GET` reuses what is stored; `POST` rebuilds. Everything else about them —
    the window, the mailbox, the task context, the response — is the same, and
    duplicating it would be two places to change the day the shape does.
    """

    now = datetime.now(UTC)
    window = _resolve_activity_period(start, end, now=now)
    requested = time.monotonic()

    logger.info(
        "activity_report_requested user_id=%s period=%s partial=%s refresh=%s",
        user.id,
        window.key,
        window.is_partial,
        refresh,
    )

    provider, mailbox = _mailbox_for(db, user_id=user.id)

    def build(correspondence) -> dict:
        return activity_service.generate_content(
            correspondence,
            owner_name=user.name,
            context=_task_context(db, user_id=user.id, period=window),
            use_model=llm_configured(),
            budget_seconds=settings.REPORT_ANALYSIS_BUDGET_SECONDS,
            now=now,
        )

    try:
        outcome = activity_service.obtain(
            db,
            user_id=user.id,
            owner_name=user.name,
            period=window,
            provider=provider,
            mailbox=mailbox,
            policy=Policy.from_settings(settings),
            now=now,
            build=build,
            refresh=refresh,
        )
    except ReportBeingGenerated as pending:
        return _pending_response(pending, period=window, now=now)

    logger.info(
        "activity_report_answered user_id=%s period=%s source=%s elapsed=%.2fs",
        user.id,
        window.key,
        outcome.source.value,
        time.monotonic() - requested,
    )

    return _activity_response(outcome, period=window, now=now)


_ACTIVITY_RESPONSES: dict[int | str, dict] = {
    **_BAD_PERIOD,
    202: {
        "model": ActivityReportPendingResponse,
        "description": "This report is already being generated",
    },
    409: {
        "model": ErrorResponse,
        "description": "No mailbox is connected for this user",
    },
}


@router.get(
    "/activity",
    response_model=ActivityReportResponse,
    responses=_ACTIVITY_RESPONSES,
    summary="The detailed activity report for a period, stored after the first",
)
def activity_report(
    user: CurrentUser,
    db: Session = Depends(get_db),
    start: date | None = Query(
        default=None,
        description=(
            "First day of the period, inclusive. Omit with `end` to report on "
            "the current month so far."
        ),
    ),
    end: date | None = Query(
        default=None,
        description="Last day of the period, inclusive. Defaults to today.",
    ),
):
    """The detailed activity report for one mailbox and one period.

    Any window: a week, a whole month, or a partial one. `start` and `end` are
    dates rather than a period key because "September so far" is not a calendar
    period and cannot be named by one — see `PeriodKind.RANGE`.

    **A report is generated once.** A window that has closed cannot change, so
    the first request builds and stores it and every request after that reads
    the stored document — no mailbox, no model, no minutes. A window still
    running is served from storage while it is fresh; after that the mailbox is
    re-read and, if nothing has arrived, the stored report is served anyway.
    `source` in the response says which of those happened.

    Answers `202` with `state: "generating"` when another request is already
    building this exact report, rather than starting a second one.

    A period longer than a week is analysed week by week and then read across,
    so a month-to-date request returns weekly sections plus a synthesis rather
    than one flat summary. The endpoint does not fail when the model is
    unavailable: the report falls back to a structural analysis of the same
    threads and says so in its header.
    """

    return _obtain_activity_report(user, db, start=start, end=end, refresh=False)


@router.post(
    "/activity",
    response_model=ActivityReportResponse,
    responses=_ACTIVITY_RESPONSES,
    summary="Rebuild the activity report for a period, ignoring what is stored",
)
def regenerate_activity_report(
    user: CurrentUser,
    db: Session = Depends(get_db),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
):
    """Rebuild this window's report and replace what is stored.

    The only way a report is rebuilt on purpose, and therefore the only thing
    the Regenerate button calls. Opening the tab, switching back to a period,
    or reloading the page all use `GET`, which reuses storage — a rebuild costs
    a mailbox read and a model call per conversation, and it should happen
    because somebody asked for it.

    Still one rebuild at a time: a request arriving while another generation is
    running gets `202` rather than a second pipeline.
    """

    return _obtain_activity_report(user, db, start=start, end=end, refresh=True)
