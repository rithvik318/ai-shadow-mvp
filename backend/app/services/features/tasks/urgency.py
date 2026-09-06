"""When a task is urgent, decided once, in one place, on the server.

The colours in the weekly report — green, amber, red — are a *view* of
something the domain already knows. This module is that something. The
frontend maps `urgency` to a colour token and does no arithmetic of its own,
because a threshold implemented twice is a threshold that disagrees with itself
the first time one copy is edited.

Three rules the milestone fixes, and one this module adds:

- **Overdue is derived, never stored.** A row saying "overdue" is only true
  until the clock moves. Everything here is a function of `due_at` and the
  moment it is asked about, so a task cannot be stale-overdue or
  stale-fine.
- **A passed deadline never means done.** Nothing here reads a due date as
  completion; a task leaves `pending` only because a person or explicit
  evidence moved it.
- **Comparison is by timestamp, not by calendar date.** "Due in three days" is
  72 hours, not "the date is three days away" — the second answer changes
  depending on what time of day it is asked.
- **Blocked is urgent.** A blocked task is not waiting, it is stuck, and the
  report exists to surface exactly that.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class TaskStatus(StrEnum):
    """The lifecycle a person controls.

    Deliberately *not* including `overdue` or `escalation_required`. Both are
    facts about the clock and the rules, not states anybody sets: storing
    either would mean a background job to keep it honest and a window in which
    it is wrong — a row reading "overdue" is only true until the clock moves.
    They appear in `display_status`, which is computed on read.

    `cancelled` is not in the product's stated vocabulary but is kept: a task
    that was abandoned is not the same as one that was done, and collapsing
    them would make the completed count a lie.
    """

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


#: Statuses that mean the work is over, either way. Neither is urgent, and
#: neither can be escalated.
RESOLVED_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})

#: Statuses that mean somebody still has to do something.
OPEN_STATUSES = frozenset({TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED})


class TaskUrgency(StrEnum):
    """How loudly the report should say something, in domain terms.

    Named for meaning rather than colour so the backend never hard-codes a
    palette. The frontend owns the mapping — normal, warning, critical become
    the theme's neutral, amber and red.
    """

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class TaskPriority(StrEnum):
    """How much it matters, independently of when it is due.

    Priority and urgency are different questions and are kept apart: a
    low-priority task can be critically overdue, and an urgent-priority task
    due next month is not yet loud.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# Two display-only states. They are strings rather than `TaskStatus` members
# precisely because nothing may ever store them.
DISPLAY_OVERDUE = "overdue"
DISPLAY_ESCALATION_REQUIRED = "escalation_required"


@dataclass(frozen=True)
class UrgencyVerdict:
    """Everything the report needs to render one task's state.

    Returned as one value rather than three calls so a caller cannot pick up a
    `display_status` from one evaluation and an `urgency` from another.
    """

    status: TaskStatus
    display_status: str
    urgency: TaskUrgency
    is_overdue: bool
    # Positive when past due, negative when still ahead of it. None with no
    # due date — which is different from zero, and the report says so.
    overdue_seconds: float | None


def _now(at: datetime | None) -> datetime:
    return at if at is not None else datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC rather than guessing a local zone.

    Rows written through `UtcDateTime` come back aware; a value constructed in
    a test might not. Comparing an aware and a naive datetime raises, and the
    failure would surface as an unrelated 500 in a report.
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def evaluate(
    *,
    status: TaskStatus,
    due_at: datetime | None,
    warning_days: int,
    now: datetime | None = None,
    escalation_required: bool = False,
) -> UrgencyVerdict:
    """Classify one task. Pure — no database, no settings, no clock of its own.

    `now` is injectable so the boundaries can be tested exactly rather than
    approximately; production passes nothing and gets the real clock.

    Precedence for `display_status`, highest first:

    1. a resolved status (`completed`, `cancelled`) — the work is over
    2. `escalation_required` — somebody other than the owner needs to act
    3. `overdue` — the deadline has passed
    4. the stored status (`todo`, `in_progress`, `blocked`)

    Escalation outranks overdue because it is the louder instruction: an
    overdue task tells its owner to get on with it, an escalated one tells them
    it is no longer theirs alone to finish.
    """

    moment = _now(now)

    if status in RESOLVED_STATUSES:
        # Resolved either way. A completed task that was once late is green:
        # the report is about what needs attention, and this does not.
        return UrgencyVerdict(
            status=status,
            display_status=status.value,
            urgency=TaskUrgency.NORMAL,
            is_overdue=False,
            overdue_seconds=None,
        )

    delta = (moment - _aware(due_at)).total_seconds() if due_at is not None else None
    overdue = delta is not None and delta > 0

    if escalation_required:
        return UrgencyVerdict(
            status=status,
            display_status=DISPLAY_ESCALATION_REQUIRED,
            urgency=TaskUrgency.CRITICAL,
            is_overdue=overdue,
            overdue_seconds=delta,
        )

    if status is TaskStatus.BLOCKED:
        # Stuck, not merely waiting — and a blocked task that is *also* late is
        # still just critical. There is nothing louder to escalate it to.
        return UrgencyVerdict(
            status=status,
            display_status=status.value,
            urgency=TaskUrgency.CRITICAL,
            is_overdue=overdue,
            overdue_seconds=delta,
        )

    if overdue:
        return UrgencyVerdict(
            status=status,
            display_status=DISPLAY_OVERDUE,
            urgency=TaskUrgency.CRITICAL,
            is_overdue=True,
            overdue_seconds=delta,
        )

    if due_at is not None and _aware(due_at) <= moment + timedelta(days=warning_days):
        return UrgencyVerdict(
            status=status,
            display_status=status.value,
            urgency=TaskUrgency.WARNING,
            is_overdue=False,
            overdue_seconds=delta,
        )

    return UrgencyVerdict(
        status=status,
        display_status=status.value,
        urgency=TaskUrgency.NORMAL,
        is_overdue=False,
        overdue_seconds=delta,
    )
