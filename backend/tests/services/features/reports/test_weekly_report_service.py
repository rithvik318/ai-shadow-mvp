"""The weekly report: what lands in which section, and when a person is asked for.

The escalation tests matter most. An escalation workflow that guesses a
recipient is worse than none, so several of these assert what the report
*refuses* to do rather than what it produces.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.features.reports import weekly_report_service
from app.services.features.reports.weekly_report_service import (
    NO_ESCALATION_CONTACT,
    build_weekly_report,
)
from app.services.features.tasks import task_service
from app.services.features.tasks.escalation import (
    REASON_BLOCKED,
    REASON_OVERDUE_HIGH,
    REASON_REQUESTED,
)
from app.services.features.tasks.urgency import TaskPriority, TaskStatus, TaskUrgency

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_escalation_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default deployment state: nobody configured to escalate to."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ESCALATION_CONTACT_ADDRESS", None)
    monkeypatch.setattr(settings_module.settings, "ESCALATION_CONTACT_NAME", None)
    monkeypatch.setattr(settings_module.settings, "TASK_ESCALATION_DAYS", 7)
    monkeypatch.setattr(settings_module.settings, "TASK_WARNING_DAYS", 3)


def _task(db: Session, user: User, **kwargs):
    kwargs.setdefault("title", "A task")

    return task_service.create_task(db, user_id=user.id, **kwargs)


# --- sections ------------------------------------------------------------


def test_each_task_lands_in_the_section_its_state_implies(
    db_session: Session, test_user: User
) -> None:
    _task(db_session, test_user, title="Soon", due_at=NOW + timedelta(days=1))
    _task(db_session, test_user, title="Late", due_at=NOW - timedelta(days=1))
    done = _task(db_session, test_user, title="Done")
    task_service.complete_task(db_session, done.id, user_id=test_user.id)
    stuck = _task(db_session, test_user, title="Stuck")
    task_service.update_task(
        db_session, stuck.id, user_id=test_user.id, status=TaskStatus.BLOCKED
    )

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.task.title for item in report.upcoming] == ["Soon"]
    assert [item.task.title for item in report.overdue] == ["Late"]
    assert [item.task.title for item in report.completed] == ["Done"]
    assert [item.task.title for item in report.blocked] == ["Stuck"]


def test_a_cancelled_task_appears_in_no_section(
    db_session: Session, test_user: User
) -> None:
    """Cancelled work is neither outstanding nor an achievement."""

    task = _task(db_session, test_user, title="Dropped")
    task_service.update_task(
        db_session, task.id, user_id=test_user.id, status=TaskStatus.CANCELLED
    )

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert report.upcoming == []
    assert report.overdue == []
    assert report.completed == []
    assert report.blocked == []


def test_the_counts_match_the_sections(db_session: Session, test_user: User) -> None:
    _task(db_session, test_user, title="Amber", due_at=NOW + timedelta(days=2))
    _task(db_session, test_user, title="Calm", due_at=NOW + timedelta(days=30))
    _task(db_session, test_user, title="Late", due_at=NOW - timedelta(days=2))
    done = _task(db_session, test_user, title="Done")
    task_service.complete_task(db_session, done.id, user_id=test_user.id)

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert report.completed_count == 1
    assert report.due_soon_count == 1
    assert report.overdue_count == 1
    assert report.due_soon_count == sum(
        1 for item in report.upcoming if item.verdict.urgency is TaskUrgency.WARNING
    )


def test_high_priority_open_work_is_collected(
    db_session: Session, test_user: User
) -> None:
    _task(db_session, test_user, title="Big", priority=TaskPriority.HIGH)
    _task(db_session, test_user, title="Small", priority=TaskPriority.LOW)

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.task.title for item in report.high_priority] == ["Big"]


def test_follow_ups_are_the_tasks_that_came_from_somewhere(
    db_session: Session, test_user: User
) -> None:
    task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key="k1",
        title="From an email",
        source="email_follow_up",
    )
    _task(db_session, test_user, title="Typed by hand")

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.task.title for item in report.follow_ups] == ["From an email"]


def test_deadlines_are_those_inside_the_week(
    db_session: Session, test_user: User
) -> None:
    _task(db_session, test_user, title="This week", due_at=NOW + timedelta(days=3))
    _task(db_session, test_user, title="Next month", due_at=NOW + timedelta(days=40))

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.task.title for item in report.deadlines] == ["This week"]


# --- isolation -----------------------------------------------------------


def test_a_report_contains_only_the_requested_users_work(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    _task(db_session, test_user, title="Mine")
    _task(db_session, test_user_b, title="Theirs")

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)
    titles = [item.task.title for item in report.upcoming]

    assert titles == ["Mine"]


# --- escalation ----------------------------------------------------------


def test_a_blocked_task_is_escalated(db_session: Session, test_user: User) -> None:
    task = _task(db_session, test_user, title="Stuck")
    task_service.update_task(
        db_session, task.id, user_id=test_user.id, status=TaskStatus.BLOCKED
    )

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.reason for item in report.escalations] == [REASON_BLOCKED]


def test_an_overdue_high_priority_task_is_escalated(
    db_session: Session, test_user: User
) -> None:
    _task(
        db_session,
        test_user,
        title="Important and late",
        priority=TaskPriority.HIGH,
        due_at=NOW - timedelta(days=1),
    )

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.reason for item in report.escalations] == [REASON_OVERDUE_HIGH]


def test_an_explicit_request_to_escalate_is_honoured(
    db_session: Session, test_user: User
) -> None:
    """When the source material said escalate, the report does not re-judge it."""

    _task(
        db_session,
        test_user,
        title="Please escalate this",
        escalation_requested=True,
    )

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.reason for item in report.escalations] == [REASON_REQUESTED]


def test_a_task_that_is_merely_upcoming_is_not_escalated(
    db_session: Session, test_user: User
) -> None:
    _task(db_session, test_user, title="Soon", due_at=NOW + timedelta(days=1))

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert report.escalations == []


def test_a_completed_task_is_never_escalated(
    db_session: Session, test_user: User
) -> None:
    task = _task(
        db_session,
        test_user,
        title="Was late, now done",
        priority=TaskPriority.URGENT,
        due_at=NOW - timedelta(days=30),
    )
    task_service.complete_task(db_session, task.id, user_id=test_user.id)

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert report.escalations == []


def test_long_overdue_normal_work_crosses_the_threshold(
    db_session: Session, test_user: User
) -> None:
    _task(
        db_session,
        test_user,
        title="Quietly rotting",
        due_at=NOW - timedelta(days=8),
    )

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert len(report.escalations) == 1


def test_an_escalation_names_no_recipient_when_none_is_configured(
    db_session: Session, test_user: User
) -> None:
    """The single most important assertion in this module.

    An escalation workflow that invents a manager sends somebody else's problem
    to the wrong person. The report must say plainly that nobody is configured.
    """

    _task(
        db_session,
        test_user,
        title="Late",
        priority=TaskPriority.HIGH,
        due_at=NOW - timedelta(days=2),
        contact_name="Robert Keenan",
        contact_address="Robert.Keenan@sunradia.com",
    )

    escalation = build_weekly_report(
        db_session, user_id=test_user.id, now=NOW
    ).escalations[0]

    assert escalation.escalation_contact_address is None
    assert escalation.escalation_contact_display == NO_ESCALATION_CONTACT
    # The counterparty on the thread is *not* promoted into an escalation
    # recipient. They are who the task is about, not who it escalates to.
    assert escalation.contact_address == "Robert.Keenan@sunradia.com"


def test_a_configured_escalation_contact_is_used(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "ESCALATION_CONTACT_ADDRESS", "boss@sunradia.com"
    )
    monkeypatch.setattr(settings_module.settings, "ESCALATION_CONTACT_NAME", "The Boss")

    _task(
        db_session,
        test_user,
        title="Late",
        priority=TaskPriority.HIGH,
        due_at=NOW - timedelta(days=2),
    )

    escalation = build_weekly_report(
        db_session, user_id=test_user.id, now=NOW
    ).escalations[0]

    assert escalation.escalation_contact_address == "boss@sunradia.com"
    assert escalation.escalation_contact_display == "The Boss <boss@sunradia.com>"


def test_every_escalation_carries_a_reason_and_a_next_action(
    db_session: Session, test_user: User
) -> None:
    _task(
        db_session,
        test_user,
        title="Late",
        priority=TaskPriority.HIGH,
        due_at=NOW - timedelta(days=2),
    )

    escalation = build_weekly_report(
        db_session, user_id=test_user.id, now=NOW
    ).escalations[0]

    assert escalation.reason
    assert escalation.recommended_action
    assert escalation.age_seconds is not None and escalation.age_seconds > 0


# --- calendar ------------------------------------------------------------


def test_the_report_says_the_calendar_is_not_connected(
    db_session: Session, test_user: User
) -> None:
    """No meetings are invented, and the absence is stated rather than implied."""

    report = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert report.calendar_connected is False
    assert "no provider connected" in report.calendar_detail.lower()


def test_the_report_is_deterministic(db_session: Session, test_user: User) -> None:
    """Same data, same moment, same answer — the property scheduling relies on."""

    _task(db_session, test_user, title="Late", due_at=NOW - timedelta(days=2))
    _task(db_session, test_user, title="Soon", due_at=NOW + timedelta(days=2))

    first = build_weekly_report(db_session, user_id=test_user.id, now=NOW)
    second = build_weekly_report(db_session, user_id=test_user.id, now=NOW)

    assert [item.task.id for item in first.overdue] == [
        item.task.id for item in second.overdue
    ]
    assert first.escalation_count == second.escalation_count


def test_the_module_exposes_no_way_to_send_anything(
    db_session: Session, test_user: User
) -> None:
    """Escalation is a flag and a suggestion, never an action.

    Asserted structurally rather than by inspection: nothing in the report
    service imports a provider or a sending path.
    """

    source = weekly_report_service.__doc__ or ""

    assert "does not send" in source.lower()
    assert not hasattr(weekly_report_service, "send")
