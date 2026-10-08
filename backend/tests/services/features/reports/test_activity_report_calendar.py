"""The Calendar section of the activity report: three states, kept apart.

A week with no meetings and a week with no calendar are different facts, and
the only version of this section that is worth printing is one that can tell a
reader which of them it is looking at.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.user import User
from app.services.features.calendar import event_service
from app.services.features.calendar.attendance import EventStatus
from app.services.features.reports import activity_report
from app.services.features.reports.period import Period, PeriodKind

START = datetime(2026, 8, 24, tzinfo=UTC)
END = datetime(2026, 8, 31, tzinfo=UTC)
NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)

PERIOD = Period(kind=PeriodKind.WEEK, start=START, end=END)


def _build(db: Session, user: User):
    report = activity_report.build(
        db,
        user_id=user.id,
        user_name=user.name,
        period=PERIOD,
        digest=None,
        now=NOW,
    )

    return next(section for section in report.sections if section.title == "Calendar")


def _meeting(db: Session, user: User, **overrides):
    values: dict = {
        "title": "Tech Park review",
        "starts_at": START + timedelta(days=2),
        "source": "graph",
        "provider": "outlook",
        "provider_event_id": "occ-1",
    }
    values.update(overrides)

    return event_service.create_event(db, user_id=user.id, **values)


def test_nothing_imported_says_the_calendar_was_not_read(
    db_session: Session, test_user: User
) -> None:
    """Not "no meetings". Nobody looked, and the section says so."""

    section = _build(db_session, test_user)

    assert section.lines == []
    assert section.note == activity_report.CALENDAR_UNAVAILABLE


def test_a_hand_typed_meeting_does_not_make_the_calendar_read(
    db_session: Session, test_user: User
) -> None:
    # A meeting somebody typed is real, but it is not evidence that the diary
    # on the screen is their actual diary.
    _meeting(
        db_session,
        test_user,
        source="manual",
        provider=None,
        provider_event_id=None,
    )

    assert _build(db_session, test_user).note == activity_report.CALENDAR_UNAVAILABLE


def test_an_imported_but_empty_week_is_said_to_be_quiet(
    db_session: Session, test_user: User
) -> None:
    """The distinction that makes the section worth having.

    The calendar was read and the week was free — which is a fact about the
    week, not about the integration.
    """

    _meeting(
        db_session,
        test_user,
        starts_at=END + timedelta(days=30),
        provider_event_id="occ-elsewhere",
    )

    section = _build(db_session, test_user)

    assert section.lines == []
    assert section.note == activity_report.CALENDAR_QUIET


def test_a_meeting_in_the_period_is_listed_with_its_time(
    db_session: Session, test_user: User
) -> None:
    _meeting(db_session, test_user)

    [line] = _build(db_session, test_user).lines

    assert line.text == "Tech Park review"
    assert "26 Aug" in (line.detail or "")


def test_an_unanswered_past_meeting_says_attendance_was_not_recorded(
    db_session: Session, test_user: User
) -> None:
    """Never "attended". Nobody has said, and the report says nobody has said."""

    _meeting(db_session, test_user)

    [line] = _build(db_session, test_user).lines

    assert line.detail is not None and "not recorded" in line.detail


def test_an_answer_somebody_gave_is_what_the_report_prints(
    db_session: Session, test_user: User
) -> None:
    event = _meeting(db_session, test_user)
    event_service.mark_attendance(
        db_session, event.id, user_id=test_user.id, status=EventStatus.ATTENDED
    )

    [line] = _build(db_session, test_user).lines

    assert line.detail is not None and line.detail.endswith("attended")


def test_the_section_is_answered_per_user(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    _meeting(db_session, test_user)

    assert _build(db_session, test_user_b).note == activity_report.CALENDAR_UNAVAILABLE
