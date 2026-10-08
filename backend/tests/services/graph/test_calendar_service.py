"""Reading Microsoft Graph's calendar, and refusing to read more than it says.

These assertions are about the translation from Graph's payload into
`GraphEvent`. Two of them are the reason the module exists at all: a meeting
must never arrive in the wrong hour, and an accepted invitation must never
arrive as attendance.
"""

from datetime import UTC, datetime

import httpx
import pytest

from app.services.graph.calendar_service import (
    PROVIDER_NAME,
    UnreadableEvent,
    list_events,
    status,
    to_event,
)
from app.services.graph.client import GraphClient
from tests.support.graph import graph_transport

MAILBOX = "sudha@sunradia.com"

SINCE = datetime(2026, 9, 7, tzinfo=UTC)
UNTIL = datetime(2026, 9, 14, tzinfo=UTC)


def _client(routes, **kwargs) -> GraphClient:
    return GraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        base_url="https://graph.example/v1.0",
        authority="https://login.example",
        transport=graph_transport(routes, **kwargs),
    )


def event_payload(
    event_id: str = "AAMk-1",
    *,
    subject: str = "Tech Park review",
    start: str = "2026-09-08T09:00:00.0000000",
    end: str | None = "2026-09-08T10:00:00.0000000",
    zone: str = "UTC",
    cancelled: bool = False,
    organiser: dict | None = None,
) -> dict:
    payload: dict = {
        "id": event_id,
        "subject": subject,
        "bodyPreview": "Agenda attached.",
        "location": {"displayName": "Meeting room 2"},
        "start": {"dateTime": start, "timeZone": zone},
        "isCancelled": cancelled,
        "organizer": organiser
        if organiser is not None
        else {"emailAddress": {"name": "Robert Keenan", "address": "r@sunradia.com"}},
    }

    if end is not None:
        payload["end"] = {"dateTime": end, "timeZone": zone}

    return payload


# --- the translation -----------------------------------------------------


class TestReadingOneEvent:
    def test_it_reads_the_fields_a_meeting_actually_has(self) -> None:
        event = to_event(event_payload())

        assert event.event_id == "AAMk-1"
        assert event.subject == "Tech Park review"
        assert event.starts_at == datetime(2026, 9, 8, 9, tzinfo=UTC)
        assert event.ends_at == datetime(2026, 9, 8, 10, tzinfo=UTC)
        assert event.location == "Meeting room 2"
        assert event.preview == "Agenda attached."

    def test_it_splits_the_organiser_into_a_name_and_an_address(self) -> None:
        # "Robert Keenan <r@sunradia.com>" is a rendering, not an address, and
        # storing it as one is how a display string ends up in a To: field.
        event = to_event(event_payload())

        assert event.organiser_name == "Robert Keenan"
        assert event.organiser_address == "r@sunradia.com"

    def test_it_says_nothing_about_attendance(self) -> None:
        # Graph reports `responseStatus: accepted` on this meeting. Accepting
        # an invitation is not going to the meeting, and `GraphEvent` has
        # nowhere to put the confusion even if a caller wanted it.
        payload = event_payload()
        payload["responseStatus"] = {"response": "accepted"}

        event = to_event(payload)

        assert not hasattr(event, "attended")
        assert not hasattr(event, "response_status")

    def test_a_meeting_with_no_stated_end_keeps_none(self) -> None:
        # Inventing an hour would make an instantaneous entry look like a
        # meeting of some length.
        assert to_event(event_payload(end=None)).ends_at is None

    def test_an_untitled_meeting_is_named_untitled_rather_than_invented(self) -> None:
        assert to_event(event_payload(subject="")).subject == "(no subject)"

    def test_a_cancelled_occurrence_is_marked_rather_than_dropped(self) -> None:
        assert to_event(event_payload(cancelled=True)).is_cancelled is True

    def test_an_organiser_graph_did_not_send_is_not_invented(self) -> None:
        event = to_event(event_payload(organiser={}))

        assert event.organiser_name is None
        assert event.organiser_address is None


class TestRefusingToGuess:
    def test_a_time_in_an_unexpected_zone_is_dropped_not_assumed_utc(self) -> None:
        # The one that matters. Graph sends a naive string plus a zone name;
        # reading a Kolkata time as UTC puts the meeting five and a half hours
        # from where it was, and nothing about the result looks wrong.
        with pytest.raises(UnreadableEvent, match="no readable start"):
            to_event(event_payload(zone="India Standard Time"))

    def test_an_event_with_no_start_is_refused(self) -> None:
        payload = event_payload()
        payload["start"] = {}

        with pytest.raises(UnreadableEvent):
            to_event(payload)

    def test_an_event_with_no_id_is_refused(self) -> None:
        # Without an id there is no key to deduplicate on, so importing it
        # twice would create two meetings out of one.
        with pytest.raises(UnreadableEvent, match="no id"):
            to_event(event_payload(event_id=""))


# --- the window ----------------------------------------------------------


class TestListingAWindow:
    def test_it_asks_calendar_view_so_a_recurring_meeting_is_expanded(self) -> None:
        # `/events` would return the stand-up once with a recurrence pattern
        # attached, and a report would count one meeting instead of five.
        routes = {
            "/calendarView": {
                "value": [
                    event_payload("occ-1", start="2026-09-08T09:00:00.0000000"),
                    event_payload("occ-2", start="2026-09-09T09:00:00.0000000"),
                ]
            }
        }

        events = list_events(_client(routes), mailbox=MAILBOX, since=SINCE, until=UNTIL)

        assert [event.event_id for event in events] == ["occ-1", "occ-2"]

    def test_it_addresses_the_mailbox_rather_than_me(self) -> None:
        """An application-only token has no signed-in user, so `/me` is an
        error rather than a shortcut — and it is the classic way an app-only
        integration is written by someone used to delegated auth.

        Asserted on the URL actually requested, not on the code that builds it.
        """

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/oauth2/v2.0/token" in url:
                return httpx.Response(
                    200, json={"access_token": "t", "expires_in": 3600}
                )

            seen.append(url)
            return httpx.Response(200, json={"value": []})

        client = GraphClient(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            base_url="https://graph.example/v1.0",
            authority="https://login.example",
            transport=httpx.MockTransport(handler),
        )

        list_events(client, mailbox=MAILBOX, since=SINCE, until=UNTIL)

        assert len(seen) == 1
        assert f"/users/{MAILBOX}/calendarView" in seen[0]
        assert "/me/" not in seen[0]

    def test_the_window_is_sent_as_utc_instants(self) -> None:
        """Graph reads `startDateTime`/`endDateTime` in the request's timezone.

        Sending a naive local time would shift the whole window, and nothing
        about the result would look wrong.
        """

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/oauth2/v2.0/token" in url:
                return httpx.Response(
                    200, json={"access_token": "t", "expires_in": 3600}
                )

            seen.append(url)
            return httpx.Response(200, json={"value": []})

        client = GraphClient(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            base_url="https://graph.example/v1.0",
            authority="https://login.example",
            transport=httpx.MockTransport(handler),
        )

        list_events(client, mailbox=MAILBOX, since=SINCE, until=UNTIL)

        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(seen[0]).query)

        assert query["startDateTime"][0].endswith("+00:00")
        assert query["endDateTime"][0].endswith("+00:00")

    def test_it_orders_by_start_and_drops_a_repeated_occurrence(self) -> None:
        routes = {
            "/calendarView": {
                "value": [
                    event_payload("occ-2", start="2026-09-09T09:00:00.0000000"),
                    event_payload("occ-1", start="2026-09-08T09:00:00.0000000"),
                    event_payload("occ-1", start="2026-09-08T09:00:00.0000000"),
                ]
            }
        }

        events = list_events(_client(routes), mailbox=MAILBOX, since=SINCE, until=UNTIL)

        assert [event.event_id for event in events] == ["occ-1", "occ-2"]

    def test_one_unreadable_row_does_not_lose_the_week(self) -> None:
        broken = event_payload("occ-bad")
        broken["start"] = {}

        routes = {"/calendarView": {"value": [broken, event_payload("occ-good")]}}

        events = list_events(_client(routes), mailbox=MAILBOX, since=SINCE, until=UNTIL)

        assert [event.event_id for event in events] == ["occ-good"]

    def test_an_empty_calendar_is_an_empty_list_not_a_failure(self) -> None:
        events = list_events(
            _client({"/calendarView": {"value": []}}),
            mailbox=MAILBOX,
            since=SINCE,
            until=UNTIL,
        )

        assert events == []


# --- the status probe ----------------------------------------------------


class TestStatus:
    def test_it_says_reachable_when_graph_answers(self) -> None:
        access = status(MAILBOX, client=_client({"/calendar": {"id": "cal-1"}}))

        assert access.usable is True
        assert access.mailbox == MAILBOX

    def test_a_403_does_not_blame_the_files_permission(self) -> None:
        """The real 403 from SunRadia's tenant, and the message it produced.

        `GraphClient` serves files, mail and calendar — three separate admin
        consents — and cannot see which one a caller was exercising. It used to
        assert `Files.Read.All` unconditionally, so a calendar refusal told the
        reader to re-grant a files permission that could not have been the
        cause. That is the same failure this project already recorded once when
        the message named `Sites.Read.All`.
        """

        access = status(
            MAILBOX,
            client=_client(
                {"/calendar": (403, {"error": {"code": "ErrorAccessDenied"}})}
            ),
        )

        assert "lacks admin-consented Files.Read.All" not in access.detail
        assert "Calendars.Read" in access.detail
        # Graph's own answer survives, so the reason is checkable.
        assert "403" in access.detail
        assert "ErrorAccessDenied" in access.detail

    def test_the_refusal_is_reported_rather_than_raised(self) -> None:
        """A screen that has to catch an exception to say "no consent" will
        eventually not. This is the whole reason `status` exists."""

        access = status(
            MAILBOX,
            client=_client(
                {"/calendar": (403, {"error": {"code": "ErrorAccessDenied"}})}
            ),
        )

        assert access.configured is True
        assert access.connected is False
        assert access.usable is False

    def test_a_403_names_the_consent_that_is_actually_missing(self) -> None:
        # Neither Files.Read.All nor Mail.Read covers a calendar, and somebody
        # re-granting either would be granting a permission that cannot help.
        access = status(
            MAILBOX,
            client=_client({"/calendar": (403, {"error": {"code": "AccessDenied"}})}),
        )

        assert access.configured is True
        assert access.connected is False
        assert "Calendars.Read" in access.detail

    def test_no_mailbox_is_reported_rather_than_probed(self) -> None:
        # An application token carries no user, so there is nothing to ask
        # about. Saying so beats a request that cannot be formed.
        access = status(None)

        assert access.configured is False
        assert access.mailbox is None
        assert "PUT /email/mailbox" in access.detail

    def test_it_never_raises_on_a_server_error(self) -> None:
        access = status(
            MAILBOX, client=_client({"/calendar": (500, {"error": {"code": "x"}})})
        )

        assert access.usable is False


def test_the_provider_name_matches_the_mail_provider() -> None:
    # The same Entra application reading the same person's Outlook. Two names
    # for one connection would make the rows harder to explain than they are.
    assert PROVIDER_NAME == "outlook"
