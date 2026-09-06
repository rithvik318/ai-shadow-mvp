"""The weekly report: one person's open work, classified by the domain.

Deterministic. Given the same tasks and the same moment, this produces the same
report — no model call, no randomness, no network. That is what makes it safe
to generate on a schedule later (see `docs/ROADMAP.md`) without a second
architecture: the report *is* a function of the task data, so scheduling it is
a question of when to call this, not of what it would say.

Three things this deliberately does not do.

**It does not invent a calendar.** Calendar access is unavailable — Graph
returns 403 for `Calendars.Read` on the configured application — so the report
carries `calendar_connected: False` and an empty event list. It never
substitutes email-derived dates for meetings, and it never says a meeting was
attended or missed. When consent is granted the events section gains a source;
nothing else about this module has to change.

**It does not decide colours.** It emits `urgency` — normal, warning, critical
— and the frontend maps those to its theme. A palette here would be a design
decision embedded in a service, and a second copy of it in the frontend would
be a threshold that eventually disagrees with this one.

**It does not send anything.** Escalation is a *flag*, a reason and a
suggested next action. Drafting is a separate, explicit user action through the
existing Email Agent composer, and sending still requires the approve-then-send
path. Nothing here has a route to a provider.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.calendar import CalendarEvent
from app.models.task import Task
from app.services.features.calendar import event_service
from app.services.features.calendar.attendance import EventVerdict
from app.services.features.tasks import task_service
from app.services.features.tasks.escalation import (
    NO_TARGET_IDENTIFIED,
    EscalationVerdict,
)
from app.services.features.tasks.urgency import (
    TaskPriority,
    TaskStatus,
    TaskUrgency,
    UrgencyVerdict,
)

#: Kept as an alias so existing callers and tests keep working. The wording now
#: lives in `tasks/escalation.py`, next to the rules that decide it.
NO_ESCALATION_CONTACT = NO_TARGET_IDENTIFIED


@dataclass(frozen=True)
class ReportTask:
    """One task as the report sees it: the row plus the domain's verdict."""

    task: Task
    verdict: UrgencyVerdict


@dataclass(frozen=True)
class ReportEvent:
    """One meeting, and whether anybody has said what happened to it."""

    event: CalendarEvent
    verdict: EventVerdict


@dataclass(frozen=True)
class Escalation:
    """One item that needs somebody other than its owner, and what to do.

    `contact_*` describe the person the *task* concerns — the counterparty on
    the thread — and are None when the source material never named one.
    `target_*` describe who this would be escalated *to*, and are None unless
    somebody configured a target. Neither is ever guessed: escalating to
    whoever happened to be on a cc line sends one person's late work to
    another person's inbox.
    """

    task: Task
    verdict: UrgencyVerdict
    escalation: EscalationVerdict

    @property
    def reason(self) -> str:
        return self.escalation.reason or ""

    @property
    def recommended_action(self) -> str:
        return self.escalation.recommended_action or ""

    @property
    def age_seconds(self) -> float | None:
        return self.verdict.overdue_seconds

    @property
    def contact_name(self) -> str | None:
        return self.escalation.contact_name

    @property
    def contact_address(self) -> str | None:
        return self.escalation.contact_address

    @property
    def escalation_contact_name(self) -> str | None:
        return self.escalation.target_name

    @property
    def escalation_contact_address(self) -> str | None:
        return self.escalation.target_address

    @property
    def escalation_contact_display(self) -> str:
        return self.escalation.target_display


@dataclass(frozen=True)
class WeeklyReport:
    """Everything the weekly workspace renders, for one person."""

    user_id: uuid.UUID
    generated_at: datetime
    period_start: datetime
    period_end: datetime

    events: list[ReportEvent] = field(default_factory=list)
    events_needing_answer: list[ReportEvent] = field(default_factory=list)

    upcoming: list[ReportTask] = field(default_factory=list)
    overdue: list[ReportTask] = field(default_factory=list)
    completed: list[ReportTask] = field(default_factory=list)
    blocked: list[ReportTask] = field(default_factory=list)
    follow_ups: list[ReportTask] = field(default_factory=list)
    high_priority: list[ReportTask] = field(default_factory=list)
    deadlines: list[ReportTask] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)

    # Counts for the summary strip. Derived here rather than in the frontend so
    # the headline and the sections can never disagree.
    completed_count: int = 0
    due_soon_count: int = 0
    overdue_count: int = 0
    escalation_count: int = 0

    #: Whether a calendar *provider* is connected. False today: the Graph
    #: application has no calendar consent. Events may still be present —
    #: entered by hand — and this flag says where they did **not** come from.
    calendar_connected: bool = False
    calendar_detail: str = (
        "Calendar: no provider connected. The Microsoft Graph application does "
        "not have calendar access, so no meetings are imported. Events shown "
        "here were entered by hand, and nothing about attendance is inferred."
    )


def _existed_by(task: Task, moment: datetime) -> bool:
    """Whether this task had been created by `moment`.

    A report of last week must not contain work created this morning. Rows have
    no history, so `created_at` is the only evidence available — and it is
    enough, because a task that did not exist cannot have been on anybody's
    list.
    """

    if task.created_at is None:
        return True

    return task.created_at.astimezone(UTC) <= moment


def _was_open_at(task: Task, moment: datetime) -> bool:
    """Whether this task was still outstanding at `moment`.

    Reconstructed from `completed_at` rather than from `status`, because
    `status` is only ever *current*: a task completed yesterday reads
    `completed` today, and asking what last week looked like has to un-ask that.
    A task with no completion stamp was open then and is open now.
    """

    if task.completed_at is None:
        return True

    return task.completed_at.astimezone(UTC) > moment


def _status_at(task: Task, moment: datetime) -> TaskStatus:
    """The status this task had at `moment`, as far as the row can say.

    Only completion is reconstructible: `completed_at` records when it
    happened, so a task completed after `moment` was not completed then. Every
    other transition — blocked, cancelled, started — leaves no timestamp, so
    the current value stands. That gap is real and is recorded in
    docs/KNOWN_ISSUES.md; the alternative is a status history table, which is a
    schema nobody has asked for and would not change any number on this screen
    today.
    """

    if task.status is TaskStatus.COMPLETED and _was_open_at(task, moment):
        return TaskStatus.TODO

    return task.status


def build_weekly_report(
    db: Session,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> WeeklyReport:
    """Build this user's report for a period. Deterministic given the rows.

    `now` is injectable so the thresholds can be tested at their exact
    boundaries; production passes nothing and gets the real clock.

    `period_start` and `period_end` select *which* week. Omit them and the
    report covers the window around `now`, which is the existing behaviour and
    what `GET /reports/weekly` serves.

    **The report is evaluated as of `min(period_end, now)`**, and that single
    idea is what makes a historical week mean something. Reading a closed week
    with today's clock would call a task completed on Tuesday "completed" in
    every week ever generated, and would put work created this morning into
    August. So the reference moment is the end of the period — or now, when the
    period has not ended, because a deadline three days into the future is not
    overdue merely because the week it falls in has not closed yet.

    What the schema can and cannot answer is worth being plain about. Tasks
    carry `created_at` and `completed_at`, so "did this exist then" and "was it
    still open then" are both reconstructible. `status` is not: a blocked task
    unblocked last Thursday reads unblocked in every historical report. That
    limitation is recorded in docs/KNOWN_ISSUES.md rather than papered over
    with a status history table nobody has asked for.
    """

    moment = now or datetime.now(UTC)

    # The moment the report describes. For the running week this is `now`; for
    # a closed one it is the instant the week ended.
    as_of = min(period_end, moment) if period_end is not None else moment
    horizon = as_of + timedelta(days=7)

    # Whether a *particular* week was asked for. Without one the report means
    # "right now" and reads the rows as they stand, which is the existing
    # contract of `GET /reports/weekly` and is deliberately unchanged. The
    # historical reconstruction below only applies when somebody selected a
    # period, so `now` keeps its old job of moving the thresholds in tests.
    scoped = period_start is not None or period_end is not None

    considered = [
        task
        for task in task_service.list_tasks(db, user_id=user_id)
        if not scoped or _existed_by(task, as_of)
    ]

    # Assessed against the status the task *had* at `as_of`, not the one it has
    # now. Without this a task completed since would be assessed as completed
    # in every past report — never overdue, never escalated — and a historical
    # week would show nothing outstanding no matter how late the work ran.
    assessments = [
        (
            task,
            task_service.assess(
                task,
                now=as_of,
                status=_status_at(task, as_of) if scoped else None,
            ),
        )
        for task in considered
    ]
    items = [
        ReportTask(task=task, verdict=verdict.urgency) for task, verdict in assessments
    ]

    upcoming: list[ReportTask] = []
    overdue: list[ReportTask] = []
    completed: list[ReportTask] = []
    blocked: list[ReportTask] = []
    follow_ups: list[ReportTask] = []
    high_priority: list[ReportTask] = []
    deadlines: list[ReportTask] = []
    escalations: list[Escalation] = []

    for item, (_, assessment) in zip(items, assessments, strict=True):
        task, verdict = item.task, item.verdict
        open_then = (
            _was_open_at(task, as_of)
            if scoped
            else verdict.status is not TaskStatus.COMPLETED
        )

        if not open_then:
            # Completed by `as_of`. Whether it belongs in *this* report is a
            # second question: a task finished in July is not an achievement of
            # the last week of August. With no period given the old behaviour
            # holds and every completed task is listed.
            finished = task.completed_at.astimezone(UTC)

            if period_start is None or finished >= period_start:
                completed.append(item)
        elif verdict.status is TaskStatus.CANCELLED:
            # Cancelled work is not open and is not an achievement. It appears
            # in no section — the report is about what needs attention.
            pass
        elif verdict.status is TaskStatus.BLOCKED:
            blocked.append(item)
        elif verdict.is_overdue:
            overdue.append(item)
        else:
            upcoming.append(item)

        if open_then and verdict.status is not TaskStatus.CANCELLED:
            if task.source != "manual":
                follow_ups.append(item)

            if task.priority in (TaskPriority.HIGH, TaskPriority.URGENT):
                high_priority.append(item)

            if task.due_at is not None and task.due_at.astimezone(UTC) <= horizon:
                deadlines.append(item)

        # Escalation is a statement about work that is still outstanding. A
        # task somebody finished cannot need escalating, whatever its due date
        # said, and reporting one would send a chaser about done work.
        if open_then and assessment.escalation.required:
            escalations.append(
                Escalation(task=task, verdict=verdict, escalation=assessment.escalation)
            )

    # A window in both directions. The report has two jobs — saying what is
    # coming, and asking what happened to what has passed — and a forward-only
    # window would never surface the meeting nobody has answered for.
    if period_start is not None:
        since, until = period_start, horizon
    else:
        since, until = event_service.week_window(moment)

    events = [
        ReportEvent(event=event, verdict=event_service.assess(event, now=as_of))
        for event in event_service.list_events(
            db, user_id=user_id, since=since, until=until
        )
    ]

    return WeeklyReport(
        user_id=user_id,
        generated_at=moment,
        period_start=since,
        period_end=period_end if period_end is not None else horizon,
        events=events,
        events_needing_answer=[item for item in events if item.verdict.needs_answer],
        upcoming=upcoming,
        overdue=overdue,
        completed=completed,
        blocked=blocked,
        follow_ups=follow_ups,
        high_priority=high_priority,
        deadlines=deadlines,
        escalations=escalations,
        completed_count=len(completed),
        due_soon_count=sum(
            1 for item in upcoming if item.verdict.urgency is TaskUrgency.WARNING
        ),
        overdue_count=len(overdue),
        escalation_count=len(escalations),
    )
