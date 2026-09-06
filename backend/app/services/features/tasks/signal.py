"""The four states a person actually reads off the Tasks page.

The lifecycle has five statuses, an urgency scale, an overdue flag, an
escalation verdict and a display status. All of that is real and the weekly
report needs it. None of it is what somebody scanning their list wants: they
want to know what is done, what is fine, what is slipping, and what is late.

So this module answers exactly that question, in four values, from two facts —
whether the task is finished, and how far past its deadline it is. Nothing
else feeds in. Priority does not, because a low-priority task four days late is
still four days late; the blocked status does not, because "blocked" is a
reason rather than a temperature.

**Lateness is counted in whole days, and that is the whole subtlety.** The
brief says exactly two days overdue is amber and more than two is red, which
only describes a band rather than a knife-edge if the count is whole days: a
task 2 days and 6 hours late is "2 days overdue" to a person reading the
screen, and it should be amber for all of that day rather than for the instant
it crosses 48 hours. So the comparison floors to whole days, and the amber band
is the whole of the third day.

Kept apart from `urgency.py` deliberately. That module decides how loudly the
*report* speaks, using a configurable warning window and an escalation
threshold; this one decides what colour a row is. They answer to different
people and would drift into each other if merged — and the report's thresholds
are deployment configuration, while these four are the product.
"""

from datetime import UTC, datetime
from enum import StrEnum

from app.services.features.tasks.urgency import RESOLVED_STATUSES, TaskStatus

#: Whole days overdue at which a task turns amber. Below this it is still grey
#: — a deadline missed this morning is not yet a problem worth colouring — and
#: above it, red.
ATTENTION_AFTER_DAYS = 2


class TaskSignal(StrEnum):
    """What colour a task row is, named for meaning rather than for the colour.

    The frontend maps these to grey, amber, red and green. Naming them
    `ATTENTION` and `URGENT` rather than `AMBER` and `RED` is the same rule the
    rest of this codebase follows: a service that knows a palette is a service
    that has to be edited when the theme changes.

    Four values, and `CANCELLED` shares `DONE` with `COMPLETED`. Neither needs
    attention, which is the only question this enum answers — the difference
    between abandoned and achieved is real and is carried by `status`, which
    the UI labels from. A fifth signal would be a colour for a distinction
    nobody makes with their eyes.
    """

    TODO = "todo"
    ATTENTION = "attention"
    URGENT = "urgent"
    DONE = "done"


def days_overdue(due_at: datetime | None, now: datetime | None = None) -> int | None:
    """Whole days past the deadline. None with no deadline, 0 when not yet late.

    Never negative: a task due next week is not "minus six days overdue", it is
    not overdue, and returning a negative number invites a caller to compare it
    against a threshold and get a colour for a task that is perfectly fine.
    """

    if due_at is None:
        return None

    moment = now if now is not None else datetime.now(UTC)
    deadline = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=UTC)

    late = (moment - deadline).total_seconds()

    if late <= 0:
        return 0

    return int(late // 86400)


def signal_for(
    *,
    status: TaskStatus,
    due_at: datetime | None,
    now: datetime | None = None,
) -> TaskSignal:
    """The colour of one task row.

    Pure, and deliberately shorter than everything around it. Four outcomes:

    - finished, either way → `DONE`
    - no deadline, or one that has not passed → `TODO`
    - fewer than two whole days late → `TODO`
    - exactly two whole days late → `ATTENTION`
    - more than two → `URGENT`

    A task with no due date is `TODO` forever, which is correct: nothing was
    ever promised about when it would be finished, and inventing a deadline to
    have something to colour is the one thing this system does not do.
    """

    if status in RESOLVED_STATUSES:
        return TaskSignal.DONE

    late = days_overdue(due_at, now)

    if late is None or late < ATTENTION_AFTER_DAYS:
        return TaskSignal.TODO

    if late == ATTENTION_AFTER_DAYS:
        return TaskSignal.ATTENTION

    return TaskSignal.URGENT
