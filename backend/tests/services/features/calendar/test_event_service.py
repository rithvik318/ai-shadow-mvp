"""Events in the database: one person's week, and nobody else's.

Two properties are asserted throughout: attendance is only ever recorded, never
concluded; and every read is scoped to one user.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import EventNotFoundError, EventValidationError
from app.models.user import User
from app.services.features.calendar import event_service
from app.services.features.calendar.attendance import AttendanceEvidence, EventStatus

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _event(db: Session, user: User, **overrides):
    values: dict = {"title": "Design review", "starts_at": NOW + timedelta(days=1)}
    values.update(overrides)

    return event_service.create_event(db, user_id=user.id, **values)


# --- creating ------------------------------------------------------------


def test_a_new_event_is_scheduled_whatever_its_date(
    db_session: Session, test_user: User
) -> None:
    """Creating a past meeting already marked attended is exactly the
    assumption this feature exists to refuse."""

    past = _event(db_session, test_user, starts_at=NOW - timedelta(days=5))

    assert past.status is EventStatus.SCHEDULED
    assert past.attendance_evidence is AttendanceEvidence.NONE
    assert past.attendance_recorded_at is None


def test_a_past_event_with_no_answer_reads_as_unknown(
    db_session: Session, test_user: User
) -> None:
    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=5))

    verdict = event_service.assess(event, now=NOW)

    assert verdict.display_status is EventStatus.UNKNOWN
    assert verdict.needs_answer is True


def test_an_organiser_is_split_into_a_name_and_an_address(
    db_session: Session, test_user: User
) -> None:
    """The Robert Keenan case, carried into the calendar. An organiser stored
    as the whole display string is not an address and must never be used as
    one."""

    event = _event(
        db_session, test_user, organiser="Robert Keenan <Robert.Keenan@sunradia.com>"
    )

    assert event.organiser_name == "Robert Keenan"
    assert event.organiser_address == "Robert.Keenan@sunradia.com"


def test_an_organiser_who_is_only_a_name_gets_no_address(
    db_session: Session, test_user: User
) -> None:
    event = _event(db_session, test_user, organiser="Robert Keenan")

    assert event.organiser_name == "Robert Keenan"
    assert event.organiser_address is None


def test_an_event_cannot_end_before_it_starts(
    db_session: Session, test_user: User
) -> None:
    with pytest.raises(EventValidationError):
        _event(db_session, test_user, ends_at=NOW - timedelta(days=1))


def test_an_empty_title_is_refused(db_session: Session, test_user: User) -> None:
    with pytest.raises(EventValidationError):
        _event(db_session, test_user, title="   ")


# --- recording attendance ------------------------------------------------


def test_a_person_can_say_they_attended(db_session: Session, test_user: User) -> None:
    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))

    marked = event_service.mark_attendance(
        db_session, event.id, user_id=test_user.id, status=EventStatus.ATTENDED
    )

    assert marked.status is EventStatus.ATTENDED
    assert marked.attendance_evidence is AttendanceEvidence.USER
    assert marked.attendance_recorded_at is not None


def test_an_answer_can_be_withdrawn(db_session: Session, test_user: User) -> None:
    """Somebody who ticked the wrong row must be able to un-tick it. Leaving a
    wrong "attended" in place is worse than any amount of missing data."""

    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))
    event_service.mark_attendance(
        db_session, event.id, user_id=test_user.id, status=EventStatus.ATTENDED
    )

    withdrawn = event_service.mark_attendance(
        db_session, event.id, user_id=test_user.id, status=EventStatus.UNKNOWN
    )

    assert withdrawn.attendance_evidence is AttendanceEvidence.NONE
    # The stamp goes with the answer it belonged to; keeping it would date an
    # answer that no longer exists.
    assert withdrawn.attendance_recorded_at is None


def test_the_source_of_an_answer_is_recorded(
    db_session: Session, test_user: User
) -> None:
    """So that "the user ticked it" and "the system concluded it" are never
    confused in the record."""

    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))

    marked = event_service.mark_attendance(
        db_session,
        event.id,
        user_id=test_user.id,
        status=EventStatus.MISSED,
        evidence=AttendanceEvidence.DERIVED,
    )

    assert marked.attendance_evidence is AttendanceEvidence.DERIVED


def test_a_note_is_kept_with_the_answer(db_session: Session, test_user: User) -> None:
    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))

    marked = event_service.mark_attendance(
        db_session,
        event.id,
        user_id=test_user.id,
        status=EventStatus.MISSED,
        note="Clashed with the site visit.",
    )

    assert marked.attendance_note == "Clashed with the site visit."


def test_editing_details_cannot_set_attendance(
    db_session: Session, test_user: User
) -> None:
    """Recording that somebody went to a meeting is a different act from
    correcting its title, and `update_event` deliberately cannot do it."""

    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))

    updated = event_service.update_event(
        db_session,
        event.id,
        user_id=test_user.id,
        title="Design review (rescheduled)",
        status=EventStatus.ATTENDED,
    )

    assert updated.title == "Design review (rescheduled)"
    assert updated.status is EventStatus.SCHEDULED


# --- isolation -----------------------------------------------------------


def test_one_user_cannot_read_anothers_event(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    event = _event(db_session, test_user)

    with pytest.raises(EventNotFoundError):
        event_service.get_event(db_session, event.id, user_id=test_user_b.id)


def test_one_user_cannot_answer_for_anothers_event(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """Attendance is a fact about a person. One person marking another's
    meeting attended would put a claim in somebody else's record."""

    event = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))

    with pytest.raises(EventNotFoundError):
        event_service.mark_attendance(
            db_session, event.id, user_id=test_user_b.id, status=EventStatus.ATTENDED
        )

    assert (
        event_service.get_event(db_session, event.id, user_id=test_user.id).status
        is EventStatus.SCHEDULED
    )


def test_one_user_only_lists_their_own_events(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    _event(db_session, test_user, title="Mine")
    _event(db_session, test_user_b, title="Theirs")

    assert [
        event.title
        for event in event_service.list_events(db_session, user_id=test_user.id)
    ] == ["Mine"]


def test_two_people_at_one_meeting_are_two_rows(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """Attendance is a fact about a person, not about a meeting, so one of
    them answering must not answer for the other."""

    mine = _event(db_session, test_user, starts_at=NOW - timedelta(days=1))
    theirs = _event(db_session, test_user_b, starts_at=NOW - timedelta(days=1))

    event_service.mark_attendance(
        db_session, mine.id, user_id=test_user.id, status=EventStatus.ATTENDED
    )

    assert (
        event_service.get_event(db_session, theirs.id, user_id=test_user_b.id).status
        is EventStatus.SCHEDULED
    )


# --- the report's window -------------------------------------------------


def test_the_window_looks_both_ways(db_session: Session, test_user: User) -> None:
    """The report has two jobs — saying what is coming, and asking what
    happened to what has passed. A forward-only window would never surface the
    meeting nobody has answered for."""

    since, until = event_service.week_window(NOW)

    assert since < NOW < until


def test_events_can_be_listed_within_a_window(
    db_session: Session, test_user: User
) -> None:
    _event(db_session, test_user, title="Long ago", starts_at=NOW - timedelta(days=60))
    _event(db_session, test_user, title="This week", starts_at=NOW + timedelta(days=1))

    since, until = event_service.week_window(NOW)
    found = event_service.list_events(
        db_session, user_id=test_user.id, since=since, until=until
    )

    assert [event.title for event in found] == ["This week"]
