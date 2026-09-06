"""Creating, reading and moving tasks — always for exactly one person.

Every function takes `user_id` and filters on it. There is no "get task by id"
without a user, so there is no shape of call that returns somebody else's work.
That is the same rule the Digital Twin follows, and for the same reason: the
boundary has to be structural, not remembered.

The one genuinely subtle piece here is `record_from_source`. Triage runs over
the same inbox repeatedly and would otherwise create a fresh task on every run.
It is written as an upsert against `source_key` — and it deliberately does
**not** overwrite the fields a person may have changed. A follow-up
re-suggested by triage must not reopen a task somebody has completed, or
silently move a deadline they adjusted.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import (
    TaskNotFoundError,
    TaskTransitionError,
    TaskValidationError,
)
from app.models.task import Task
from app.services.features.tasks import escalation
from app.services.features.tasks.escalation import EscalationVerdict
from app.services.features.tasks.urgency import (
    TaskPriority,
    TaskStatus,
    UrgencyVerdict,
    evaluate,
)

logger = logging.getLogger(__name__)

MAX_TITLE = 500


@dataclass(frozen=True)
class TaskVerdict:
    """Everything derived about one task, produced together.

    Urgency and escalation are returned as one value rather than by two calls
    because they are answers to the same question asked at the same moment. A
    caller that fetched them separately could render an `escalation_required`
    badge beside a `normal` urgency, which is not a state the domain has.
    """

    urgency: UrgencyVerdict
    escalation: EscalationVerdict


def assess(
    task: Task,
    *,
    now: datetime | None = None,
    status: TaskStatus | None = None,
) -> TaskVerdict:
    """The canonical reading of one task, using the configured thresholds.

    The single place the domain answer is produced. Routes, the weekly report
    and any future consumer all come through here, so none of them can develop
    a private opinion about what "due soon" or "needs escalating" means.

    Escalation is evaluated first because `display_status` depends on it: an
    escalated task reads as `escalation_required` rather than as `overdue`.

    `status` overrides the row's own, and exists for exactly one caller: the
    weekly report reconstructing a *past* week. `task.status` is only ever
    current, so a task completed this morning would read as completed in every
    historical report and nothing would ever have been outstanding. The report
    works out what the status was from `completed_at` and says so here, rather
    than reimplementing the thresholds against a status it corrected itself.
    """

    reading = status if status is not None else task.status

    provisional = evaluate(
        status=reading,
        due_at=task.due_at,
        warning_days=settings.TASK_WARNING_DAYS,
        now=now,
    )

    verdict = escalation.evaluate(
        status=reading,
        priority=task.priority,
        is_overdue=provisional.is_overdue,
        overdue_seconds=provisional.overdue_seconds,
        escalation_requested=task.escalation_requested,
        source=task.source,
        escalation_seconds=settings.TASK_ESCALATION_DAYS * 86400.0,
        contact_name=task.contact_name,
        contact_address=task.contact_address,
        configured_target_name=settings.ESCALATION_CONTACT_NAME,
        configured_target_address=settings.ESCALATION_CONTACT_ADDRESS,
    )

    if not verdict.required:
        return TaskVerdict(urgency=provisional, escalation=verdict)

    return TaskVerdict(
        urgency=evaluate(
            status=reading,
            due_at=task.due_at,
            warning_days=settings.TASK_WARNING_DAYS,
            now=now,
            escalation_required=True,
        ),
        escalation=verdict,
    )


def classify(task: Task, *, now: datetime | None = None) -> UrgencyVerdict:
    """The urgency half of `assess`, for callers that need nothing else."""

    return assess(task, now=now).urgency


def _clean_title(title: str) -> str:
    cleaned = (title or "").strip()

    if not cleaned:
        raise TaskValidationError("A task needs a title.")

    if len(cleaned) > MAX_TITLE:
        raise TaskValidationError(
            f"A task title is at most {MAX_TITLE} characters; that one is "
            f"{len(cleaned)}."
        )

    return cleaned


def list_tasks(
    db: Session,
    *,
    user_id: uuid.UUID,
    statuses: list[TaskStatus] | None = None,
) -> list[Task]:
    """This user's tasks, soonest deadline first, undated last.

    Ordering is part of the contract rather than the caller's problem: every
    consumer wants the same thing, which is the next thing that needs doing.
    """

    query = select(Task).where(Task.user_id == user_id)

    if statuses:
        query = query.where(Task.status.in_(statuses))

    tasks = list(db.execute(query).scalars().all())

    # Sorted in Python rather than SQL because "nulls last" is spelled
    # differently in SQLite and Postgres, and the lists here are one person's
    # open work — tens of rows, not thousands.
    return sorted(
        tasks,
        key=lambda task: (
            task.due_at is None,
            task.due_at.astimezone(UTC)
            if task.due_at is not None
            else datetime.max.replace(tzinfo=UTC),
            task.created_at,
        ),
    )


def get_task(db: Session, task_id: uuid.UUID, *, user_id: uuid.UUID) -> Task:
    """One task belonging to this user, or 404.

    A task belonging to somebody else is *not found*, not *forbidden*. Telling
    a caller a row exists but is not theirs confirms the existence of another
    person's data, which is itself a leak.
    """

    task = db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    ).scalar_one_or_none()

    if task is None:
        raise TaskNotFoundError(f"No task {task_id} belongs to this user.")

    return task


def create_task(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
    due_at: datetime | None = None,
    source: str = "manual",
    source_key: str | None = None,
    source_provider: str | None = None,
    source_message_id: str | None = None,
    source_thread_id: str | None = None,
    source_assessment_id: uuid.UUID | None = None,
    contact_name: str | None = None,
    contact_address: str | None = None,
    escalation_requested: bool = False,
    escalation_note: str | None = None,
) -> Task:
    """Create one task for this user."""

    task = Task(
        user_id=user_id,
        title=_clean_title(title),
        description=(description or "").strip() or None,
        priority=priority,
        due_at=due_at,
        source=source,
        source_key=source_key,
        source_provider=source_provider,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        source_assessment_id=source_assessment_id,
        contact_name=contact_name,
        contact_address=contact_address,
        escalation_requested=escalation_requested,
        escalation_note=escalation_note,
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


def update_task(
    db: Session,
    task_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    **changes: object,
) -> Task:
    """Change one task. Completion is handled here, and only here.

    Moving to `completed` stamps `completed_at`; moving away from it clears the
    stamp. Keeping those two in step anywhere else would eventually produce a
    task that is completed with no completion time, or the reverse.
    """

    task = get_task(db, task_id, user_id=user_id)

    if "title" in changes:
        task.title = _clean_title(str(changes["title"]))

    for field in (
        "description",
        "priority",
        "due_at",
        "contact_name",
        "contact_address",
        "escalation_requested",
        "escalation_note",
    ):
        if field in changes:
            setattr(task, field, changes[field])

    if "status" in changes:
        _set_status(task, TaskStatus(changes["status"]))

    db.commit()
    db.refresh(task)

    return task


#: Where each status may go next. Same-state is always allowed, so pressing a
#: button twice is a no-op rather than an error.
#:
#: `COMPLETED` is nearly terminal: the one move out of it is back to `TODO`.
#:
#: That asymmetry is deliberate and is the whole of the lifecycle rule. Marking
#: a finished task **to do** is an explicit correction — somebody says the work
#: came back — and it clears `completed_at`, because a reopened task that still
#: claims a completion time is a lie. **Starting** one is not a correction; it
#: is a lifecycle step that assumes the work was outstanding, and allowing it
#: would let "done" drift into "in progress" as a side effect of pressing the
#: button on the wrong row. So Start is refused with a 409 that says why, and
#: the deliberate route back stays open.
_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset(
        {
            TaskStatus.IN_PROGRESS,
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {
            TaskStatus.TODO,
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.TODO,
            TaskStatus.IN_PROGRESS,
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.CANCELLED: frozenset({TaskStatus.TODO, TaskStatus.IN_PROGRESS}),
    TaskStatus.COMPLETED: frozenset({TaskStatus.TODO}),
}

_DISPLAY = {
    TaskStatus.TODO: "to do",
    TaskStatus.IN_PROGRESS: "in progress",
    TaskStatus.COMPLETED: "completed",
    TaskStatus.BLOCKED: "blocked",
    TaskStatus.CANCELLED: "cancelled",
}


def _set_status(task: Task, status: TaskStatus) -> None:
    current = task.status

    if status is not current and status not in _ALLOWED_TRANSITIONS[current]:
        raise TaskTransitionError(
            f"This task is {_DISPLAY[current]} and cannot be moved to "
            f"{_DISPLAY[status]}."
            + (
                " Set it back to to-do first if the work has come back."
                if current is TaskStatus.COMPLETED
                else ""
            )
        )

    task.status = status

    if status is TaskStatus.COMPLETED:
        # Only set if it was not already completed, so re-completing does not
        # rewrite the moment the work actually finished.
        task.completed_at = task.completed_at or datetime.now(UTC)
    else:
        task.completed_at = None


def complete_task(db: Session, task_id: uuid.UUID, *, user_id: uuid.UUID) -> Task:
    """Mark a task done because a person said so.

    The only route to `completed` besides an explicit status update. Nothing in
    this codebase infers completion from a sent email, a quiet thread or a
    passed deadline.
    """

    return update_task(db, task_id, user_id=user_id, status=TaskStatus.COMPLETED)


def delete_task(db: Session, task_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    task = get_task(db, task_id, user_id=user_id)

    db.delete(task)
    db.commit()


def find_by_source_key(
    db: Session, *, user_id: uuid.UUID, source_key: str
) -> Task | None:
    return db.execute(
        select(Task).where(Task.user_id == user_id, Task.source_key == source_key)
    ).scalar_one_or_none()


def record_from_source(
    db: Session,
    *,
    user_id: uuid.UUID,
    source_key: str,
    title: str,
    source: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
    due_at: datetime | None = None,
    source_provider: str | None = None,
    source_message_id: str | None = None,
    source_thread_id: str | None = None,
    source_assessment_id: uuid.UUID | None = None,
    contact_name: str | None = None,
    contact_address: str | None = None,
    escalation_requested: bool = False,
    escalation_note: str | None = None,
) -> tuple[Task, bool]:
    """Create the task for this source, or return the one that already exists.

    Returns `(task, created)`. Idempotent by `source_key`, which is what stops
    a second triage run over the same inbox producing a second copy of every
    follow-up.

    **An existing task is not overwritten.** A person may have completed it,
    changed its deadline or re-prioritised it, and re-running triage is not a
    reason to undo any of that. Only genuinely absent fields are filled in —
    information the task never had, arriving late.
    """

    existing = find_by_source_key(db, user_id=user_id, source_key=source_key)

    if existing is not None:
        changed = False

        # Late-arriving detail only. Never the title, status, priority or due
        # date: those are the fields a person edits.
        for field, value in (
            ("description", description),
            ("contact_name", contact_name),
            ("contact_address", contact_address),
            ("source_assessment_id", source_assessment_id),
            ("source_thread_id", source_thread_id),
        ):
            if value is not None and getattr(existing, field) is None:
                setattr(existing, field, value)
                changed = True

        if changed:
            db.commit()
            db.refresh(existing)

        return existing, False

    task = create_task(
        db,
        user_id=user_id,
        title=title,
        description=description,
        priority=priority,
        due_at=due_at,
        source=source,
        source_key=source_key,
        source_provider=source_provider,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        source_assessment_id=source_assessment_id,
        contact_name=contact_name,
        contact_address=contact_address,
        escalation_requested=escalation_requested,
        escalation_note=escalation_note,
    )

    logger.info(
        "task_created_from_source",
        extra={"user_id": str(user_id), "source": source},
    )

    return task, True
