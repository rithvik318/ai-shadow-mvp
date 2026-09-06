"""Whether a meeting actually happened, answered honestly.

Pure, and short, because the whole value is in one refusal.

**Presence in a calendar is not attendance.** A meeting existing in Graph means
somebody put it there. It does not mean the person went, and a report that says
"attended" on that basis is worse than one that says nothing: it launders an
assumption into a record, and the person reading it has no way to tell which
rows are observed and which are guessed.

So there are exactly two ways an event becomes `attended` or `missed`:

- a person says so, explicitly, through the API; or
- evidence good enough to name is recorded alongside it.

Everything else is `unknown`, including — especially — a past meeting nobody
has said anything about. `unknown` is not a failure state or a gap in the data.
It is the correct answer, and the report shows it as a question to answer
rather than as an error.

The one thing derived here is the *display* status of a scheduled event whose
time has passed. It stays `scheduled` in the database, because that is what was
recorded, and reads as `unknown`, because that is what is known. Storing the
transition would need a background job to sweep events as they age, and would
be wrong for the window between the meeting ending and the sweep running.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class EventStatus(StrEnum):
    """What is recorded about one event.

    `SCHEDULED` and `CANCELLED` describe the meeting; `ATTENDED`, `MISSED` and
    `UNKNOWN` describe the person's relationship to it. They share one field
    because a reader wants one answer per row, and because the states are
    mutually exclusive in practice.
    """

    SCHEDULED = "scheduled"
    ATTENDED = "attended"
    MISSED = "missed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AttendanceEvidence(StrEnum):
    """How an attendance answer was arrived at.

    Stored next to the status so that "the user ticked it" and "the system
    concluded it" are never confused. Nothing currently produces `DERIVED`;
    the member exists so that when a source of real evidence appears it has a
    name, rather than arriving disguised as a user's own answer.
    """

    #: A person said so.
    USER = "user"
    #: Concluded from recorded evidence — a join record, a transcript.
    DERIVED = "derived"
    #: Nobody has said, and nothing has been concluded.
    NONE = "none"


#: Statuses that answer "did this happen for this person?".
ANSWERED_STATUSES = frozenset({EventStatus.ATTENDED, EventStatus.MISSED})

#: Statuses a person may set directly. `SCHEDULED` is included so a wrongly
#: marked event can be put back; `UNKNOWN` so an answer can be withdrawn.
SETTABLE_STATUSES = frozenset(
    {
        EventStatus.SCHEDULED,
        EventStatus.ATTENDED,
        EventStatus.MISSED,
        EventStatus.CANCELLED,
        EventStatus.UNKNOWN,
    }
)


@dataclass(frozen=True)
class EventVerdict:
    """How one event should read right now."""

    status: EventStatus
    display_status: EventStatus
    #: True when the event is over and nobody has said what happened. The one
    #: thing the report should actually prompt about.
    needs_answer: bool
    is_past: bool


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC rather than guessing a local zone."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def evaluate(
    *,
    status: EventStatus,
    starts_at: datetime,
    ends_at: datetime | None = None,
    now: datetime | None = None,
) -> EventVerdict:
    """Say how an event reads, without ever inventing an attendance answer.

    An event is "past" once it has ended, or once it has started when no end
    time was recorded — an event with no end is treated as instantaneous rather
    than as running forever, because the alternative is a meeting that can
    never be asked about.
    """

    moment = now or datetime.now(UTC)
    boundary = _aware(ends_at) if ends_at is not None else _aware(starts_at)
    is_past = boundary <= moment

    if status is EventStatus.CANCELLED:
        # It did not happen, for everybody. There is nothing to ask.
        return EventVerdict(
            status=status,
            display_status=status,
            needs_answer=False,
            is_past=is_past,
        )

    if status in ANSWERED_STATUSES:
        return EventVerdict(
            status=status, display_status=status, needs_answer=False, is_past=is_past
        )

    if not is_past:
        # Still ahead. `unknown` on a future meeting is not a question worth
        # asking, so it reads as scheduled either way.
        return EventVerdict(
            status=status,
            display_status=EventStatus.SCHEDULED,
            needs_answer=False,
            is_past=False,
        )

    # Over, and nobody has said what happened. This is the refusal the module
    # exists for: not "attended", not "missed", and not silence either.
    return EventVerdict(
        status=status,
        display_status=EventStatus.UNKNOWN,
        needs_answer=True,
        is_past=True,
    )
