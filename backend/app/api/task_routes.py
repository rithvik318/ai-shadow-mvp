"""Tasks and the weekly report, always for the calling user.

Every route resolves its owner from `CurrentUser` and passes that id down. No
route reads a user id from a path or a body, so there is no endpoint here that
can be pointed at somebody else's list.

The urgency a response carries is computed here through
`task_service.classify`, never in the client. That is what keeps one threshold
in one place: change `TASK_WARNING_DAYS` and every consumer moves together.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.api.renderers import (
    event_response,
    task_response,
    weekly_report_response,
)
from app.core.exceptions import TaskNotFoundError, TaskValidationError
from app.database.session import get_db
from app.models.email import EmailAssessment
from app.schemas.document_schema import ErrorResponse
from app.schemas.task_schema import (
    AttendanceRequest,
    EventCreateRequest,
    EventListResponse,
    EventResponse,
    EventUpdateRequest,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
    WeeklyReportResponse,
)
from app.services.features.calendar import event_service
from app.services.features.reports import weekly_report_service
from app.services.features.tasks import email_task_service, task_service
from app.services.features.tasks.urgency import TaskStatus

router = APIRouter(tags=["tasks"])

_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "No such task for this user"}
}


# The renderers moved to `app/api/renderers.py` when the report history
# endpoints began rendering the same weekly report. These two names are kept as
# aliases so every call site below reads exactly as it did before the move.
_to_response = task_response
_to_event_response = event_response


# --- tasks ---------------------------------------------------------------


@router.get("/tasks", response_model=TaskListResponse, summary="This user's tasks")
def list_tasks(
    user: CurrentUser,
    db: Session = Depends(get_db),
    task_status: TaskStatus | None = None,
) -> TaskListResponse:
    """Every task belonging to the caller, soonest deadline first."""

    tasks = task_service.list_tasks(
        db, user_id=user.id, statuses=[task_status] if task_status else None
    )

    return TaskListResponse(
        items=[_to_response(task) for task in tasks], total=len(tasks)
    )


@router.post(
    "/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
)
def create_task(
    request: TaskCreateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TaskResponse:
    task = task_service.create_task(
        db,
        user_id=user.id,
        title=request.title,
        description=request.description,
        priority=request.priority,
        due_at=request.due_at,
        contact_name=request.contact_name,
        contact_address=request.contact_address,
    )

    return _to_response(task)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    responses=_NOT_FOUND,
    summary="One task",
)
def get_task(
    task_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TaskResponse:
    return _to_response(task_service.get_task(db, task_id, user_id=user.id))


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    responses=_NOT_FOUND,
    summary="Change a task",
)
def update_task(
    task_id: uuid.UUID,
    request: TaskUpdateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TaskResponse:
    """Apply the fields that were sent, and only those.

    `exclude_unset` matters: without it, every field the client omitted would
    arrive as None and clear a value the person never touched.
    """

    task = task_service.update_task(
        db, task_id, user_id=user.id, **request.model_dump(exclude_unset=True)
    )

    return _to_response(task)


@router.post(
    "/tasks/{task_id}/complete",
    response_model=TaskResponse,
    responses=_NOT_FOUND,
    summary="Mark a task complete",
)
def complete_task(
    task_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TaskResponse:
    """Completion as an explicit act.

    This is what the checkbox in the weekly report calls. It exists as its own
    endpoint because completing is the single most common change and because
    the act should be unmistakable in an access log.
    """

    return _to_response(task_service.complete_task(db, task_id, user_id=user.id))


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    summary="Delete a task",
)
def delete_task(
    task_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> None:
    task_service.delete_task(db, task_id, user_id=user.id)


# --- the weekly report ---------------------------------------------------


@router.get(
    "/reports/weekly",
    response_model=WeeklyReportResponse,
    summary="This user's week: due, late, done, blocked and escalations",
)
def weekly_report(
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> WeeklyReportResponse:
    """Build the report for the caller.

    Deterministic from the task data, which is what will let it be generated on
    a schedule without a second implementation.

    Calendar events are absent and *said* to be absent. The Graph application
    has no calendar consent, so this reports `calendar_connected: false` with a
    plain explanation rather than an empty list that would read as a free week.
    """

    report = weekly_report_service.build_weekly_report(db, user_id=user.id)

    return weekly_report_response(report)


# --- events --------------------------------------------------------------
#
# Separate from tasks because they answer a different question, and the
# attendance write is its own endpoint because recording that somebody went to
# a meeting is a different act from correcting its title — an access log in
# which the two are indistinguishable is worth less.

_EVENT_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "No such event for this user"}
}


@router.get("/events", response_model=EventListResponse, summary="This user's events")
def list_events(
    user: CurrentUser,
    db: Session = Depends(get_db),
    since: datetime | None = None,
    until: datetime | None = None,
) -> EventListResponse:
    events = event_service.list_events(db, user_id=user.id, since=since, until=until)

    return EventListResponse(
        items=[_to_event_response(event) for event in events], total=len(events)
    )


@router.post(
    "/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record that a meeting exists",
)
def create_event(
    request: EventCreateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> EventResponse:
    """Create an event. It is `scheduled`; nothing here can mark it attended."""

    return _to_event_response(
        event_service.create_event(
            db,
            user_id=user.id,
            title=request.title,
            starts_at=request.starts_at,
            ends_at=request.ends_at,
            location=request.location,
            description=request.description,
            organiser=request.organiser,
        )
    )


@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    responses=_EVENT_NOT_FOUND,
    summary="One event",
)
def get_event(
    event_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> EventResponse:
    return _to_event_response(event_service.get_event(db, event_id, user_id=user.id))


@router.patch(
    "/events/{event_id}",
    response_model=EventResponse,
    responses=_EVENT_NOT_FOUND,
    summary="Change an event's details",
)
def update_event(
    event_id: uuid.UUID,
    request: EventUpdateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> EventResponse:
    return _to_event_response(
        event_service.update_event(
            db, event_id, user_id=user.id, **request.model_dump(exclude_unset=True)
        )
    )


@router.post(
    "/events/{event_id}/attendance",
    response_model=EventResponse,
    responses=_EVENT_NOT_FOUND,
    summary="Record whether this person attended",
)
def mark_attendance(
    event_id: uuid.UUID,
    request: AttendanceRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> EventResponse:
    """The only route by which an event becomes attended or missed.

    Nothing infers attendance from a meeting merely existing. `unknown` is
    accepted and withdraws an answer, because somebody who ticked the wrong
    row must be able to un-tick it — a wrong "attended" is worse than a gap.
    """

    return _to_event_response(
        event_service.mark_attendance(
            db, event_id, user_id=user.id, status=request.status, note=request.note
        )
    )


@router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_EVENT_NOT_FOUND,
    summary="Delete an event",
)
def delete_event(
    event_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> None:
    event_service.delete_event(db, event_id, user_id=user.id)


@router.post(
    "/tasks/from-follow-ups",
    response_model=TaskListResponse,
    summary="Turn this user's open follow-ups into tasks",
)
def tasks_from_follow_ups(
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TaskListResponse:
    """Create a task for each unhandled follow-up that does not have one.

    Explicit rather than automatic. Triage runs whenever an inbox is opened, and
    creating work as a side effect of *looking* would mean a person's task list
    changed because they read their mail.

    Idempotent: running it repeatedly produces the same list, because each task
    is keyed by the message it came from. Returns every follow-up task the user
    now has, not only the ones this call created — the caller wants the state,
    not the diff.
    """

    assessments = (
        db.execute(
            select(EmailAssessment).where(
                EmailAssessment.user_id == user.id,
                EmailAssessment.follow_up_recommended.is_(True),
                EmailAssessment.handled.is_(False),
            )
        )
        .scalars()
        .all()
    )

    for assessment in assessments:
        email_task_service.sync_follow_up_task(db, assessment, user_id=user.id)

    tasks = [
        task
        for task in task_service.list_tasks(db, user_id=user.id)
        if task.source == email_task_service.SOURCE_FOLLOW_UP
    ]

    return TaskListResponse(
        items=[_to_response(task) for task in tasks], total=len(tasks)
    )


@router.post(
    "/tasks/from-follow-up/{assessment_id}",
    response_model=TaskResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "No such triaged message for this user",
        },
        422: {
            "model": ErrorResponse,
            "description": "Triage did not identify an action for this message",
        },
    },
    summary="Add one triaged email to this user's tasks",
)
def task_from_follow_up(
    assessment_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TaskResponse:
    """What the "Add to Tasks" button on a triaged email calls.

    The bulk endpoint above answers "make tasks for everything", which is a
    useful sweep and a poor button: a person who has just read one message
    wants that one on their list, and being told "4 tasks created" tells them
    nothing about the one they were looking at. This returns **the task**, so
    the caller can show it.

    Deliberately the same `sync_follow_up_task` the sweep uses, so both produce
    identical tasks and neither can create a second copy: `source_key` is the
    message's identity and is unique per user. Pressing the button twice
    returns the task that already exists rather than a duplicate or an error —
    the person's intent is satisfied either way.

    `422` when triage did not recommend a follow-up. That is not a failure: the
    message was read and judged not to need action, and inventing a task from
    it would put work on somebody's list that nothing asked for.
    """

    assessment = db.execute(
        select(EmailAssessment).where(
            EmailAssessment.id == assessment_id,
            # Scoped, so a valid id belonging to somebody else is a 404 rather
            # than a route into their mail.
            EmailAssessment.user_id == user.id,
        )
    ).scalar_one_or_none()

    if assessment is None:
        raise TaskNotFoundError("No triaged message with that id belongs to this user.")

    task, _ = email_task_service.sync_follow_up_task(db, assessment, user_id=user.id)

    if task is None:
        raise TaskValidationError(
            "Triage did not identify an action for this message, so there is "
            "nothing to add. Re-triage it if you think it needs one."
        )

    return _to_response(task)
