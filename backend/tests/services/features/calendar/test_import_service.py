"""Importing real meetings, and the four things an import must not do.

It must not invent attendance, it must not overwrite an answer somebody gave,
it must not create a second copy of a meeting it has already seen, and it must
not report an empty result when it could not read a calendar at all. Each has a
test here, and each is a way the feature would look like it was working while
being wrong.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import CalendarUnavailableError
from app.models.user import User
from app.services.features.calendar import event_service, import_service
from app.services.features.calendar.attendance import AttendanceEvidence, EventStatus
from app.services.features.email import mailbox_config_service
from app.services.graph.client import GraphClient
from tests.services.graph.test_calendar_service import event_payload
from tests.support.graph import graph_transport

MAILBOX = "sudha@sunradia.com"

SINCE = datetime(2026, 9, 7, tzinfo=UTC)
UNTIL = datetime(2026, 9, 14, tzinfo=UTC)


def _client(*payloads: dict) -> GraphClient:
    return GraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        base_url="https://graph.example/v1.0",
        authority="https://login.example",
        transport=graph_transport({"/calendarView": {"value": list(payloads)}}),
    )


@pytest.fixture
def connected(db_session: Session, test_user: User) -> User:
    """A user with a mailbox, which is what makes a calendar addressable."""

    mailbox_config_service.set_mailbox(
        db_session, user_id=test_user.id, address=MAILBOX, provider="outlook"
    )

    return test_user


def _import(db: Session, user: User, *payloads: dict):
    return import_service.import_events(
        db,
        user_id=user.id,
        since=SINCE,
        until=UNTIL,
        client=_client(*payloads),
    )


# --- what an import records ----------------------------------------------


class TestImporting:
    def test_a_meeting_arrives_with_its_details(
        self, db_session: Session, connected: User
    ) -> None:
        result = _import(db_session, connected, event_payload("occ-1"))

        assert result.created == 1
        assert result.mailbox == MAILBOX

        [event] = event_service.list_events(db_session, user_id=connected.id)
        assert event.title == "Tech Park review"
        assert event.starts_at == datetime(2026, 9, 8, 9, tzinfo=UTC)
        assert event.location == "Meeting room 2"
        assert event.organiser_address == "r@sunradia.com"

    def test_an_imported_meeting_is_labelled_as_imported(
        self, db_session: Session, connected: User
    ) -> None:
        # `source` is how a report says where a meeting came from, and how a
        # person can tell one they typed from one that arrived.
        _import(db_session, connected, event_payload("occ-1"))

        [event] = event_service.list_events(db_session, user_id=connected.id)
        assert event.source == "graph"
        assert event.provider == "outlook"
        assert event.provider_event_id == "occ-1"

    def test_a_past_meeting_is_imported_scheduled_not_attended(
        self, db_session: Session, connected: User
    ) -> None:
        # The single assumption this whole feature is arranged to refuse. The
        # meeting is over; nobody has said whether they went.
        _import(
            db_session,
            connected,
            event_payload("occ-1", start="2026-09-07T09:00:00.0000000"),
        )

        [event] = event_service.list_events(db_session, user_id=connected.id)
        assert event.status is EventStatus.SCHEDULED
        assert event.attendance_evidence is AttendanceEvidence.NONE
        assert event.attendance_recorded_at is None
        assert (
            event_service.assess(event, now=UNTIL).display_status is EventStatus.UNKNOWN
        )

    def test_a_cancelled_meeting_is_kept_and_said_to_be_cancelled(
        self, db_session: Session, connected: User
    ) -> None:
        # Deleting it would silently remove a meeting somebody may already have
        # planned around; leaving it unmarked would show it as still happening.
        result = _import(db_session, connected, event_payload("occ-1", cancelled=True))

        assert result.cancelled == 1
        [event] = event_service.list_events(db_session, user_id=connected.id)
        assert event.title.startswith("Cancelled: ")

    def test_cancellations_are_a_subset_of_what_was_seen(
        self, db_session: Session, connected: User
    ) -> None:
        result = _import(
            db_session,
            connected,
            event_payload("occ-1"),
            event_payload("occ-2", cancelled=True),
        )

        assert (result.created, result.cancelled, result.total) == (2, 1, 2)


# --- running it twice ----------------------------------------------------


class TestIdempotency:
    def test_the_same_window_twice_creates_nothing_the_second_time(
        self, db_session: Session, connected: User
    ) -> None:
        _import(db_session, connected, event_payload("occ-1"))
        second = _import(db_session, connected, event_payload("occ-1"))

        assert (second.created, second.unchanged) == (0, 1)
        assert len(event_service.list_events(db_session, user_id=connected.id)) == 1

    def test_a_moved_meeting_is_updated_in_place(
        self, db_session: Session, connected: User
    ) -> None:
        _import(db_session, connected, event_payload("occ-1"))
        result = _import(
            db_session,
            connected,
            event_payload("occ-1", start="2026-09-08T14:00:00.0000000", end=None),
        )

        assert result.updated == 1
        [event] = event_service.list_events(db_session, user_id=connected.id)
        assert event.starts_at == datetime(2026, 9, 8, 14, tzinfo=UTC)

    def test_an_import_never_erases_an_answer_somebody_gave(
        self, db_session: Session, connected: User
    ) -> None:
        # The worst thing an import could do. Somebody records that they went;
        # the next sync must not quietly un-record it.
        _import(db_session, connected, event_payload("occ-1"))
        [event] = event_service.list_events(db_session, user_id=connected.id)

        event_service.mark_attendance(
            db_session,
            event.id,
            user_id=connected.id,
            status=EventStatus.ATTENDED,
            note="Went in person.",
        )

        _import(
            db_session,
            connected,
            event_payload("occ-1", subject="Tech Park review (moved)"),
        )

        [after] = event_service.list_events(db_session, user_id=connected.id)
        assert after.status is EventStatus.ATTENDED
        assert after.attendance_note == "Went in person."
        assert after.attendance_evidence is AttendanceEvidence.USER
        # The details Graph owns did move.
        assert after.title == "Tech Park review (moved)"

    def test_a_manual_event_is_untouched_by_an_import(
        self, db_session: Session, connected: User
    ) -> None:
        typed = event_service.create_event(
            db_session,
            user_id=connected.id,
            title="Coffee with Robert",
            starts_at=datetime(2026, 9, 9, 15, tzinfo=UTC),
        )

        _import(db_session, connected, event_payload("occ-1"))

        assert (
            event_service.get_event(db_session, typed.id, user_id=connected.id).title
            == "Coffee with Robert"
        )


# --- isolation and refusals ----------------------------------------------


class TestRefusals:
    def test_no_mailbox_is_refused_rather_than_reported_as_an_empty_week(
        self, db_session: Session, test_user: User
    ) -> None:
        # An empty result would be indistinguishable from a genuinely free
        # week, and a report built on it would be claiming a calendar was read.
        with pytest.raises(CalendarUnavailableError, match="No mailbox"):
            import_service.import_events(
                db_session, user_id=test_user.id, since=SINCE, until=UNTIL
            )

    def test_a_backwards_window_is_refused(
        self, db_session: Session, connected: User
    ) -> None:
        with pytest.raises(CalendarUnavailableError):
            import_service.import_events(
                db_session,
                user_id=connected.id,
                since=UNTIL,
                until=SINCE,
                client=_client(),
            )

    def test_an_unbounded_window_is_refused(
        self, db_session: Session, connected: User
    ) -> None:
        with pytest.raises(CalendarUnavailableError, match="at most"):
            import_service.import_events(
                db_session,
                user_id=connected.id,
                since=SINCE,
                until=SINCE + timedelta(days=import_service.MAX_WINDOW_DAYS + 1),
                client=_client(),
            )

    def test_an_import_lands_only_in_the_calling_user_s_calendar(
        self, db_session: Session, connected: User, test_user_b: User
    ) -> None:
        _import(db_session, connected, event_payload("occ-1"))

        assert event_service.list_events(db_session, user_id=test_user_b.id) == []


# --- what a report asks about --------------------------------------------


class TestHasImportedEvents:
    def test_it_is_false_before_anything_is_imported(
        self, db_session: Session, test_user: User
    ) -> None:
        assert not import_service.has_imported_events(db_session, user_id=test_user.id)

    def test_a_hand_typed_event_does_not_make_a_calendar_connected(
        self, db_session: Session, test_user: User
    ) -> None:
        # The distinction the flag exists for: events on the screen do not mean
        # a calendar was read.
        event_service.create_event(
            db_session,
            user_id=test_user.id,
            title="Coffee",
            starts_at=datetime(2026, 9, 9, 15, tzinfo=UTC),
        )

        assert not import_service.has_imported_events(db_session, user_id=test_user.id)

    def test_it_is_true_once_a_meeting_has_arrived_from_a_provider(
        self, db_session: Session, connected: User
    ) -> None:
        _import(db_session, connected, event_payload("occ-1"))

        assert (
            import_service.has_imported_events(db_session, user_id=connected.id) is True
        )

    def test_it_is_answered_per_user(
        self, db_session: Session, connected: User, test_user_b: User
    ) -> None:
        _import(db_session, connected, event_payload("occ-1"))

        assert (
            import_service.has_imported_events(db_session, user_id=test_user_b.id)
            is False
        )


class TestAccess:
    def test_it_says_no_mailbox_without_ever_reaching_graph(
        self, db_session: Session, test_user: User
    ) -> None:
        # No credentials are configured in the test environment, so a probe
        # that tried to build a client would be a network call in a unit test.
        access = import_service.access(db_session, user_id=test_user.id)

        assert access.usable is False
        assert access.mailbox is None
