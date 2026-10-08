"""Bringing one person's real meetings into their week.

The bridge between `app/services/graph/calendar_service.py`, which knows Graph
and nothing about a person, and `CalendarEvent`, which knows a person and
nothing about Graph. It resolves the mailbox the way every other per-user Graph
call does — through `mailbox_config_service`, so a user with no mailbox
connected is *not connected* rather than quietly reading somebody else's
calendar.

**An import never decides attendance.** It records that a meeting exists and
what it was called, exactly as `create_event` does for one typed in by hand. A
meeting that has passed with nobody's answer stays `scheduled` and reads as
`unknown`. Graph does know whether an invitation was accepted; accepting an
invitation is not going to the meeting, and treating one as the other is the
single assumption `attendance.py` exists to refuse.

**Re-importing the same window changes nothing.** `(user_id,
provider_event_id)` is unique in the schema already, so an occurrence is
matched to the row it produced before and updated in place. Details Graph owns
— title, times, location, organiser — are refreshed; anything a person recorded
is left alone, because an import must never overwrite an answer somebody gave.

**No migration was needed for any of this.** `provider`, `provider_event_id`
and that constraint were added when the model was written, against the day
consent arrived. This is that day.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.exceptions import CalendarUnavailableError
from app.models.calendar import CalendarEvent
from app.services.features.calendar.attendance import AttendanceEvidence, EventStatus
from app.services.features.email import mailbox_config_service
from app.services.graph import calendar_service
from app.services.graph.calendar_service import (
    PROVIDER_NAME,
    CalendarAccess,
    GraphEvent,
)
from app.services.graph.client import GraphClient

logger = logging.getLogger(__name__)

#: What `source` says for a row this module created, distinguishing it from
#: `manual`. The report uses it to say where a meeting came from.
SOURCE = "graph"

#: How far a single import may reach. A window is an argument, not a policy,
#: but an unbounded one against a busy calendar is a page-walk nobody intended
#: to start from a report.
MAX_WINDOW_DAYS = 400

#: `CalendarEvent.title` is `String(500)`, and a cancellation prefix must not
#: be what pushes a long subject over it.
_TITLE_LIMIT = 500


@dataclass(frozen=True)
class ImportResult:
    """What one import actually did. Every number is a count of rows touched."""

    mailbox: str
    since: datetime
    until: datetime
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    cancelled: int = 0

    @property
    def total(self) -> int:
        """Occurrences seen. `cancelled` is a subset of these, not a fourth."""

        return self.created + self.updated + self.unchanged


def access(db: Session, *, user_id: uuid.UUID) -> CalendarAccess:
    """Whether this user's calendar can be read, without ever raising.

    Makes one Graph call, so it belongs on a status endpoint and not in the
    middle of building a report.
    """

    return calendar_service.status(
        mailbox_config_service.resolve_address(db, user_id=user_id)
    )


def has_imported_events(db: Session, *, user_id: uuid.UUID) -> bool:
    """Whether any meeting in this user's calendar came from a provider.

    Deliberately a question about *rows*, not about consent: it is answerable
    from the database with no network call, which is what lets a report state
    where its meetings came from while staying deterministic and offline.
    """

    return bool(
        db.execute(
            select(
                exists().where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.provider.is_not(None),
                )
            )
        ).scalar()
    )


def _existing(
    db: Session, *, user_id: uuid.UUID, provider_event_ids: list[str]
) -> dict[str, CalendarEvent]:
    """The rows this window has already produced, keyed by provider id."""

    if not provider_event_ids:
        return {}

    rows = (
        db.execute(
            select(CalendarEvent).where(
                CalendarEvent.user_id == user_id,
                CalendarEvent.provider_event_id.in_(provider_event_ids),
            )
        )
        .scalars()
        .all()
    )

    return {row.provider_event_id: row for row in rows if row.provider_event_id}


def _title(event: GraphEvent) -> str:
    """The title to store, cancellation stated rather than implied.

    A cancelled occurrence is kept — deleting it would silently remove a
    meeting somebody may already have acted on — but it must not sit in a week
    looking like it is still happening.
    """

    if event.is_cancelled and not event.subject.lower().startswith("canceled"):
        return f"Cancelled: {event.subject}"[:_TITLE_LIMIT]

    return event.subject[:_TITLE_LIMIT]


def _apply(row: CalendarEvent, event: GraphEvent) -> bool:
    """Copy the fields Graph owns onto a row. True if anything changed.

    The fields a person owns — `status`, `attendance_evidence`,
    `attendance_note`, `attendance_recorded_at` — are deliberately absent from
    this list. An import that refreshed them would erase an answer somebody
    gave, which is the one thing worse than a missing meeting.
    """

    changes = {
        "title": _title(event),
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "location": event.location,
        "description": event.preview,
        "organiser_name": event.organiser_name,
        "organiser_address": event.organiser_address,
    }

    changed = False

    for field, value in changes.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True

    return changed


def import_events(
    db: Session,
    *,
    user_id: uuid.UUID,
    since: datetime,
    until: datetime,
    client: GraphClient | None = None,
) -> ImportResult:
    """Import one window of this user's real calendar. Idempotent.

    Raises `CalendarUnavailableError` when there is no mailbox to read or Graph
    will not answer — never returns a result describing zero meetings, because
    a caller could not tell that apart from a genuinely empty week, and a
    report that did would be claiming a calendar was read.
    """

    start = since.astimezone(UTC)
    end = until.astimezone(UTC)

    if end <= start:
        raise CalendarUnavailableError("A calendar window has to end after it starts.")

    if (end - start).days > MAX_WINDOW_DAYS:
        raise CalendarUnavailableError(
            f"A calendar window is at most {MAX_WINDOW_DAYS} days; that one is "
            f"{(end - start).days}."
        )

    mailbox = mailbox_config_service.resolve_address(db, user_id=user_id)

    if not mailbox:
        raise CalendarUnavailableError(
            "No mailbox is connected for this user, and an application token "
            "carries no user — so there is no calendar to read. Connect one "
            "with PUT /email/mailbox."
        )

    if client is None:
        client = GraphClient.from_settings()

    events = calendar_service.list_events(
        client, mailbox=mailbox, since=start, until=end
    )
    rows = _existing(
        db, user_id=user_id, provider_event_ids=[event.event_id for event in events]
    )

    created = updated = unchanged = cancelled = 0

    for event in events:
        row = rows.get(event.event_id)

        if row is None:
            row = CalendarEvent(
                user_id=user_id,
                title=_title(event),
                description=event.preview,
                location=event.location,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                # Scheduled whatever its date, exactly as `create_event` does.
                # A past meeting imported as `attended` would be the assumption
                # this feature refuses.
                status=EventStatus.SCHEDULED,
                attendance_evidence=AttendanceEvidence.NONE,
                organiser_name=event.organiser_name,
                organiser_address=event.organiser_address,
                source=SOURCE,
                provider=PROVIDER_NAME,
                provider_event_id=event.event_id,
            )
            db.add(row)
            created += 1
        elif _apply(row, event):
            updated += 1
        else:
            unchanged += 1

        if event.is_cancelled:
            cancelled += 1

    db.commit()

    logger.info(
        "calendar_events_imported",
        extra={
            "user_id": str(user_id),
            # Not `created`: `logging` puts the record's own creation time
            # there, and an `extra` key of that name raises rather than being
            # recorded.
            "created_events": created,
            "updated": updated,
            "unchanged": unchanged,
        },
    )

    return ImportResult(
        mailbox=mailbox,
        since=start,
        until=end,
        created=created,
        updated=updated,
        unchanged=unchanged,
        cancelled=cancelled,
    )
