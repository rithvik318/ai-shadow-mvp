"""Microsoft Graph's calendar, translated and nothing more.

The same split `outlook_provider.py` draws around mail, drawn again around
meetings: Graph's JSON in, a neutral dataclass out. There is no database here,
no user, and no knowledge of attendance — `CalendarEvent` rows and the rule
that attendance is recorded rather than assumed live one layer up, in
`app/services/features/calendar/`.

**No second authentication.** The token, the throttling retries, the paging and
the error translation come from `GraphClient`, which OneDrive sync and the
Email Agent already share. One Entra application, one token cache. Sharing the
application is not sharing its consent, though: `Files.Read.All` and
`Mail.Read` do not grant calendar access, so `Calendars.Read` has to be granted
to the same application before anything here returns a meeting. `status()`
exists to say so in a sentence rather than letting a 403 surface from a report.

**`calendarView`, not `events`.** `/events` returns a recurring meeting as one
row carrying a recurrence pattern, which a report would either miscount as a
single meeting or have to expand itself — a calendar engine nobody asked for.
`/calendarView` asks Graph to expand it, so a weekly stand-up in a seven-day
window arrives as the occurrences it actually was.

**A cancelled occurrence is reported as cancelled, not dropped.** Discarding it
here would leave an already-imported row describing a meeting that is no longer
happening, with nothing to say so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.exceptions import (
    GraphAuthError,
    GraphError,
    SyncNotConfiguredError,
)
from app.services.graph.client import GraphClient

logger = logging.getLogger(__name__)

#: What lands in `CalendarEvent.provider`. The same word the mail provider
#: uses, because it is the same application reading the same person's Outlook.
PROVIDER_NAME = "outlook"

#: Asked for explicitly so one round trip returns what an import needs and no
#: message bodies. `bodyPreview` rather than `body`: a report shows a line, and
#: a full meeting body is somebody's notes.
_LIST_FIELDS = (
    "id,subject,bodyPreview,location,start,end,organizer,isCancelled,seriesMasterId"
)

#: Graph caps a `calendarView` page; asking for more is ignored rather than
#: honoured, and paging handles the rest either way.
_PAGE_SIZE = 100


def _parse_datetime(value: str | None, zone: str | None) -> datetime | None:
    """Graph's `{dateTime, timeZone}` pair as an aware UTC instant.

    Graph returns a *naive* string plus a separate zone name. Absent a
    `Prefer: outlook.timezone` header — and nothing here sends one — that zone
    is UTC, which is why no conversion table is needed. The zone is read only
    to notice when it is something else, in which case the value is dropped
    rather than silently treated as UTC: a meeting placed in the wrong hour is
    worse in a report than a meeting that is missing from it, because nothing
    about it looks wrong.
    """

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("graph_calendar_unparsable_timestamp")
        return None

    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)

    if zone and zone.strip().lower() not in {"utc", "gmt standard time", "z"}:
        logger.warning("graph_calendar_unexpected_timezone", extra={"zone": zone})
        return None

    return parsed.replace(tzinfo=UTC)


def _organiser(payload: dict | None) -> tuple[str | None, str | None]:
    """Graph's `{emailAddress: {name, address}}`, split into name and address.

    Split for the same reason assessments split their sender: "Robert Keenan
    <r@sunradia.com>" is a rendering, not an address, and must never be used as
    one.
    """

    inner = (payload or {}).get("emailAddress") or {}
    address = inner.get("address")
    name = inner.get("name")

    return (str(name) if name else None, str(address) if address else None)


@dataclass(frozen=True)
class GraphEvent:
    """One occurrence of one meeting, as Graph described it.

    Deliberately says nothing about attendance. Graph knows whether an invite
    was accepted, which is not the same fact as whether a person went — reading
    one as the other is exactly the assumption `attendance.py` exists to refuse.
    """

    event_id: str
    subject: str
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = None
    preview: str | None = None
    organiser_name: str | None = None
    organiser_address: str | None = None
    is_cancelled: bool = False


class UnreadableEvent(ValueError):
    """A payload that cannot be made into a `GraphEvent`.

    Raised only for the two facts an event cannot do without — an identity and
    a start. Everything else is optional in Graph's own schema and is optional
    here.
    """


def to_event(payload: dict) -> GraphEvent:
    """Map one Graph `event` resource onto the neutral type.

    Module-level and public so the mapping is testable against recorded
    payloads without a client or a transport. Every field is read defensively:
    Graph omits what was not requested, and a missing key is a normal response.
    """

    event_id = str(payload.get("id") or "").strip()

    if not event_id:
        raise UnreadableEvent("A calendar event arrived with no id.")

    start = payload.get("start") or {}
    starts_at = _parse_datetime(start.get("dateTime"), start.get("timeZone"))

    if starts_at is None:
        raise UnreadableEvent(f"Calendar event {event_id} has no readable start time.")

    end = payload.get("end") or {}
    name, address = _organiser(payload.get("organizer"))

    location = (payload.get("location") or {}).get("displayName")
    preview = payload.get("bodyPreview")

    return GraphEvent(
        event_id=event_id,
        # An untitled meeting is a real thing in Outlook, and the row needs a
        # title, so it is named as untitled rather than invented.
        subject=str(payload.get("subject") or "").strip() or "(no subject)",
        starts_at=starts_at,
        ends_at=_parse_datetime(end.get("dateTime"), end.get("timeZone")),
        location=str(location).strip() or None if location else None,
        preview=str(preview).strip() or None if preview else None,
        organiser_name=name,
        organiser_address=address,
        is_cancelled=payload.get("isCancelled") is True,
    )


def list_events(
    client: GraphClient,
    *,
    mailbox: str,
    since: datetime,
    until: datetime,
) -> list[GraphEvent]:
    """Every occurrence in one mailbox's calendar between two instants.

    Ordered by start, earliest first, and deduplicated on Graph's id — a window
    that straddles a series boundary can return the same occurrence twice.

    An event Graph returns that cannot be read is logged and skipped. Refusing
    the whole window because one row had an odd start would lose a week of real
    meetings to one bad one.
    """

    events: dict[str, GraphEvent] = {}

    payloads = client.paged(
        f"/users/{mailbox}/calendarView",
        {
            "startDateTime": since.astimezone(UTC).isoformat(),
            "endDateTime": until.astimezone(UTC).isoformat(),
            "$select": _LIST_FIELDS,
            "$orderby": "start/dateTime",
            "$top": _PAGE_SIZE,
        },
    )

    for payload in payloads:
        try:
            event = to_event(payload)
        except UnreadableEvent as exc:
            logger.warning("graph_calendar_event_skipped", extra={"reason": str(exc)})
            continue

        events[event.event_id] = event

    return sorted(events.values(), key=lambda event: (event.starts_at, event.event_id))


@dataclass(frozen=True)
class CalendarAccess:
    """Whether this deployment can actually read a calendar, and why not."""

    configured: bool
    connected: bool
    mailbox: str | None
    detail: str

    @property
    def usable(self) -> bool:
        return self.configured and self.connected


def status(mailbox: str | None, *, client: GraphClient | None = None) -> CalendarAccess:
    """Describe calendar access. Never raises — that is the point of it.

    One cheap call that proves the token mints *and* that calendar consent was
    granted: a calendar read fails with 403 when `Calendars.Read` is missing,
    which is the failure most likely to be sitting in wait, because neither the
    files consent nor the mail consent covers it.
    """

    address = (mailbox or "").strip()

    if not address:
        return CalendarAccess(
            configured=False,
            connected=False,
            mailbox=None,
            detail=(
                "No mailbox is connected for this user, and an application "
                "token carries no user — so there is no calendar to read. "
                "Connect one with PUT /email/mailbox."
            ),
        )

    if client is None:
        try:
            client = GraphClient.from_settings()
        except SyncNotConfiguredError as exc:
            return CalendarAccess(
                configured=False,
                connected=False,
                mailbox=address,
                detail=(
                    f"Microsoft Graph is not configured. {exc} The calendar "
                    "shares the Entra application used for OneDrive "
                    "synchronisation."
                ),
            )

    try:
        client.get(f"/users/{address}/calendar", {"$select": "id"})
    except GraphAuthError as exc:
        # Composed here rather than by prefixing the client's message, because
        # this is the layer that knows the call was a calendar read. The client
        # serves three APIs with three separate consents and names none of them.
        answered = (
            f"HTTP {exc.status_code}{f', {exc.graph_code}' if exc.graph_code else ''}"
            if exc.status_code
            else "a refusal"
        )

        return CalendarAccess(
            configured=True,
            connected=False,
            mailbox=address,
            detail=(
                f"Graph refused the calendar read for {address} ({answered}). "
                "Calendar access is a separate consent from files and mail: "
                "the application needs the APPLICATION permission "
                "Calendars.Read, granted and admin-consented. Neither "
                "Files.Read.All nor Mail.Read covers it, so re-granting either "
                "cannot help."
            ),
        )
    except GraphError as exc:
        return CalendarAccess(
            configured=True, connected=False, mailbox=address, detail=str(exc)
        )

    return CalendarAccess(
        configured=True,
        connected=True,
        mailbox=address,
        detail=f"Microsoft Graph calendar reachable for {address}.",
    )
