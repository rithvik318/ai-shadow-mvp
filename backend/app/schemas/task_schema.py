"""The HTTP shape of a task, and of the weekly report built from tasks.

`status`, `urgency` and `is_overdue` are all sent, and each answers a different
question:

- `status` is the lifecycle a person controls — todo, in_progress, completed,
  blocked, cancelled — and is what a write sets.
- `display_status` folds in the clock and the escalation rules, so it can also
  be `overdue` or `escalation_required`. Neither is storable: both are only
  true until the clock moves.
- `urgency` is the canonical severity the frontend maps to a colour.

The frontend renders `urgency` and never recomputes it from a date. That is the
whole point of sending it: one threshold, decided on the server.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.features.calendar.attendance import AttendanceEvidence, EventStatus
from app.services.features.tasks.escalation import TargetSource
from app.services.features.tasks.signal import TaskSignal
from app.services.features.tasks.urgency import (
    TaskPriority,
    TaskStatus,
    TaskUrgency,
)

MAX_TITLE = 500
MAX_DESCRIPTION = 20_000


class TaskResponse(BaseModel):
    """One task, with the domain's verdict on it already applied."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None = None

    status: TaskStatus
    display_status: str
    urgency: TaskUrgency
    is_overdue: bool

    #: What colour the Tasks page paints this row, and how many whole days
    #: late it is. Both derived on read from completion and the deadline — see
    #: `services/features/tasks/signal.py`. The frontend renders them and
    #: computes neither, so the two-day boundary exists once.
    signal: TaskSignal
    days_overdue: int | None = None
    priority: TaskPriority

    due_at: datetime | None = None
    completed_at: datetime | None = None

    source: str
    source_message_id: str | None = None
    source_thread_id: str | None = None

    # Split, for the same reason the assessment's sender is split: the address
    # is what a reply goes to, the display string is only ever shown.
    contact_name: str | None = None
    contact_address: str | None = None
    contact_display: str | None = None

    # What a person asked for, and their own words about it. Stored.
    escalation_requested: bool = False
    escalation_note: str | None = None

    # What the rules concluded, right now. Derived, and never written back.
    escalation_required: bool = False
    escalation_reason: str | None = None
    escalation_action: str | None = None

    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int


class TaskCreateRequest(BaseModel):
    """Create a task for the calling user.

    No `user_id` field, deliberately. Ownership comes from the authenticated
    identity; a body-supplied id would let anyone write into anyone's list.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: datetime | None = None
    contact_name: str | None = Field(default=None, max_length=255)
    contact_address: str | None = Field(default=None, max_length=320)


class TaskUpdateRequest(BaseModel):
    """Change a task. Every field optional; absent means unchanged.

    Setting `status` to `completed` is the explicit act that completes a task.
    Nothing infers it — not a sent email, not a quiet thread, not a passed due
    date.
    """

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    contact_name: str | None = Field(default=None, max_length=255)
    contact_address: str | None = Field(default=None, max_length=320)
    escalation_requested: bool | None = None
    escalation_note: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


class EscalationResponse(BaseModel):
    """One item needing a person's attention, and what to do about it.

    `escalation_contact_display` is the string to show. When nobody has been
    configured it reads "Escalation target not identified" — the UI shows that
    plainly rather than offering to send to a guessed recipient.
    """

    task: TaskResponse
    reason: str
    age_seconds: float | None = None
    recommended_action: str

    # The counterparty the task concerns — usually whoever has not replied.
    contact_name: str | None = None
    contact_address: str | None = None

    # Who it would be escalated *to*. Never derived from the thread.
    escalation_contact_name: str | None = None
    escalation_contact_address: str | None = None
    escalation_contact_display: str
    target_source: TargetSource = TargetSource.NOT_IDENTIFIED


class EventResponse(BaseModel):
    """One meeting, and whether anybody has said what happened to it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None = None
    location: str | None = None

    starts_at: datetime
    ends_at: datetime | None = None

    #: What was recorded.
    status: EventStatus
    #: How it reads now. A finished meeting nobody has answered for is
    #: `unknown` here while staying `scheduled` above.
    display_status: EventStatus
    #: True when the event is over and nobody has said what happened — the one
    #: thing the report should actually prompt about.
    needs_answer: bool
    is_past: bool

    attendance_evidence: AttendanceEvidence
    attendance_recorded_at: datetime | None = None
    attendance_note: str | None = None

    organiser_name: str | None = None
    organiser_address: str | None = None

    source: str
    created_at: datetime
    updated_at: datetime


class EventListResponse(BaseModel):
    items: list[EventResponse]
    total: int


class EventCreateRequest(BaseModel):
    """Record that a meeting exists. Attendance is not settable here.

    A new event is always `scheduled`, whatever its date — creating a past
    meeting already marked `attended` is exactly the assumption this feature
    exists to refuse.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    #: Accepts `"Name <addr>"` or a bare address; split on the way in, so an
    #: organiser is never stored as though the display string were an address.
    organiser: str | None = Field(default=None, max_length=500)


class EventUpdateRequest(BaseModel):
    """Change an event's details. Deliberately cannot set attendance."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    location: str | None = Field(default=None, max_length=500)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class AttendanceRequest(BaseModel):
    """A person saying what happened.

    The only route by which an event becomes `attended` or `missed`. `unknown`
    is allowed and withdraws an answer: somebody who ticked the wrong row must
    be able to un-tick it.
    """

    status: EventStatus
    note: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


class ReportSummary(BaseModel):
    """The headline strip. Computed with the sections, so they cannot disagree."""

    completed_count: int
    due_soon_count: int
    overdue_count: int
    escalation_count: int
    events_needing_answer_count: int


class WeeklyReportResponse(BaseModel):
    """This user's week: what is done, due, late, stuck and needs a person.

    Counts are computed alongside the sections rather than by the client, so
    the headline strip and the lists below it cannot disagree.
    """

    generated_at: datetime
    period_start: datetime
    period_end: datetime

    #: Kept alongside `summary` rather than replaced by it: these four fields
    #: are the existing contract and removing them would break a client to
    #: gain nothing.
    completed_count: int
    due_soon_count: int
    overdue_count: int
    escalation_count: int
    summary: ReportSummary

    upcoming: list[TaskResponse]
    overdue: list[TaskResponse]
    completed: list[TaskResponse]
    blocked: list[TaskResponse]
    follow_ups: list[TaskResponse]
    high_priority: list[TaskResponse]
    deadlines: list[TaskResponse]
    escalations: list[EscalationResponse]

    # Stated rather than implied. An empty events list could mean "no meetings"
    # or "no calendar"; these two fields say which, and the UI shows the
    # difference instead of implying a free week.
    calendar_connected: bool
    calendar_detail: str
    events: list[EventResponse] = Field(default_factory=list)
    events_needing_answer: list[EventResponse] = Field(default_factory=list)
