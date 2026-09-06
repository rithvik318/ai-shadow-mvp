"""Reading and recording one person's meetings.

Every function takes `user_id` and filters on it, exactly as tasks do. There is
no "get event by id" without a user, so no call shape returns somebody else's
week.

The rule the whole module is arranged around lives in `attendance.py`: nothing
here ever concludes that a person attended a meeting. `mark_attendance` records
what a person said and stamps who said it; imports record that a meeting
exists and nothing more. A past meeting with no answer stays `scheduled` in the
row and reads as `unknown`, which is the honest answer and the one the report
asks about.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import EventNotFoundError, EventValidationError
from app.models.calendar import CalendarEvent
from app.services.features.calendar.attendance import (
    ANSWERED_STATUSES,
    SETTABLE_STATUSES,
    AttendanceEvidence,
    EventStatus,
    EventVerdict,
)
from app.services.features.calendar.attendance import evaluate as evaluate_attendance
from app.services.features.email.address import parse_address

logger = logging.getLogger(__name__)

MAX_TITLE = 500


def assess(event: CalendarEvent, *, now: datetime | None = None) -> EventVerdict:
    """The canonical reading of one event. One place, so nothing disagrees."""

    return evaluate_attendance(
        status=event.status,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        now=now,
    )


def _clean_title(title: str) -> str:
    cleaned = (title or "").strip()

    if not cleaned:
        raise EventValidationError("An event needs a title.")

    if len(cleaned) > MAX_TITLE:
        raise EventValidationError(
            f"An event title is at most {MAX_TITLE} characters; that one is "
            f"{len(cleaned)}."
        )

    return cleaned


def list_events(
    db: Session,
    *,
    user_id: uuid.UUID,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[CalendarEvent]:
    """This user's events, earliest first, optionally within a window."""

    query = select(CalendarEvent).where(CalendarEvent.user_id == user_id)

    if since is not None:
        query = query.where(CalendarEvent.starts_at >= since)
    if until is not None:
        query = query.where(CalendarEvent.starts_at <= until)

    return sorted(
        db.execute(query).scalars().all(),
        key=lambda event: (event.starts_at.astimezone(UTC), event.created_at),
    )


def get_event(db: Session, event_id: uuid.UUID, *, user_id: uuid.UUID) -> CalendarEvent:
    """One event belonging to this user, or 404.

    Somebody else's event is *not found*, not *forbidden*: confirming a row
    exists but is not yours is itself a disclosure.
    """

    event = db.execute(
        select(CalendarEvent).where(
            CalendarEvent.id == event_id, CalendarEvent.user_id == user_id
        )
    ).scalar_one_or_none()

    if event is None:
        raise EventNotFoundError(f"No event {event_id} belongs to this user.")

    return event


def create_event(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: str,
    starts_at: datetime,
    ends_at: datetime | None = None,
    location: str | None = None,
    description: str | None = None,
    organiser: str | None = None,
    source: str = "manual",
    provider: str | None = None,
    provider_event_id: str | None = None,
) -> CalendarEvent:
    """Record that a meeting exists. Never that anybody went to it.

    A new event is `scheduled` whatever its date. Creating a past meeting
    already marked `attended` would be exactly the assumption this feature
    exists to refuse — it stays `scheduled`, reads as `unknown`, and waits for
    somebody to answer.
    """

    if ends_at is not None and ends_at < starts_at:
        raise EventValidationError("An event cannot end before it starts.")

    # Structured, for the same reason assessments split their sender: an
    # organiser rendered as "Robert Keenan <r@sunradia.com>" is not an address
    # and must never be used as one.
    parsed = parse_address(organiser)

    event = CalendarEvent(
        user_id=user_id,
        title=_clean_title(title),
        description=(description or "").strip() or None,
        location=(location or "").strip() or None,
        starts_at=starts_at,
        ends_at=ends_at,
        status=EventStatus.SCHEDULED,
        attendance_evidence=AttendanceEvidence.NONE,
        organiser_name=parsed.name if parsed else None,
        organiser_address=(parsed.address or None) if parsed else None,
        source=source,
        provider=provider,
        provider_event_id=provider_event_id,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return event


def mark_attendance(
    db: Session,
    event_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    status: EventStatus,
    note: str | None = None,
    evidence: AttendanceEvidence = AttendanceEvidence.USER,
) -> CalendarEvent:
    """Record what a person says happened.

    `evidence` defaults to `USER` because a person is the only source this
    system currently has. It is a parameter rather than a constant so that a
    future derived source arrives labelled as derived, instead of appearing in
    the record as though somebody had answered by hand.

    Setting `unknown` is allowed and withdraws an answer — a person who ticked
    the wrong row must be able to un-tick it, and leaving them with a wrong
    "attended" would be worse than any amount of missing data.
    """

    if status not in SETTABLE_STATUSES:
        raise EventValidationError(
            f"{status.value!r} is not a status a person can set. Choose one of: "
            + ", ".join(sorted(item.value for item in SETTABLE_STATUSES))
            + "."
        )

    event = get_event(db, event_id, user_id=user_id)

    event.status = status
    event.attendance_note = (note or "").strip() or None

    if status in ANSWERED_STATUSES:
        event.attendance_evidence = evidence
        event.attendance_recorded_at = datetime.now(UTC)
    else:
        # Back to "nobody has said". The stamp goes with the answer it
        # belonged to; leaving it would date an answer that no longer exists.
        event.attendance_evidence = AttendanceEvidence.NONE
        event.attendance_recorded_at = None

    db.commit()
    db.refresh(event)

    logger.info(
        "event_attendance_recorded",
        extra={"user_id": str(user_id), "status": status.value},
    )

    return event


def update_event(
    db: Session,
    event_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    **changes: object,
) -> CalendarEvent:
    """Change an event's details. Attendance is not settable through here.

    Separated from `mark_attendance` on purpose: recording that somebody went
    to a meeting is a different act from correcting its title, and an access
    log in which the two are indistinguishable is worth less.
    """

    event = get_event(db, event_id, user_id=user_id)

    if "title" in changes:
        event.title = _clean_title(str(changes["title"]))

    for field in ("description", "location"):
        if field in changes:
            value = changes[field]
            setattr(event, field, (str(value).strip() or None) if value else None)

    for field in ("starts_at", "ends_at"):
        if field in changes:
            setattr(event, field, changes[field])

    if event.ends_at is not None and event.ends_at < event.starts_at:
        raise EventValidationError("An event cannot end before it starts.")

    db.commit()
    db.refresh(event)

    return event


def delete_event(db: Session, event_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    db.delete(get_event(db, event_id, user_id=user_id))
    db.commit()


def week_window(
    now: datetime | None = None, *, days: int = 7
) -> tuple[datetime, datetime]:
    """The report's window: the last seven days and the next seven.

    Both directions, because the report has two jobs — telling somebody what is
    coming, and asking them what happened to what has passed. A forward-only
    window would never surface the meeting nobody has answered for.
    """

    moment = now or datetime.now(UTC)

    return moment - timedelta(days=days), moment + timedelta(days=days)
