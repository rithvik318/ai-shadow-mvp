"""When a task needs somebody else, and who that is.

Pure-function tests. Two properties matter more than the rest: escalation is
never concluded for finished work, and a target is never invented.
"""

import pytest

from app.services.features.tasks.escalation import (
    NO_TARGET_IDENTIFIED,
    REASON_BLOCKED,
    REASON_LONG_OVERDUE,
    REASON_OVERDUE_HIGH,
    REASON_REQUESTED,
    REASON_UNANSWERED,
    TargetSource,
    evaluate,
)
from app.services.features.tasks.urgency import TaskPriority, TaskStatus

WEEK = 7 * 86400.0


def _verdict(**overrides):
    values: dict = {
        "status": TaskStatus.TODO,
        "priority": TaskPriority.NORMAL,
        "is_overdue": False,
        "overdue_seconds": None,
        "escalation_requested": False,
        "source": "manual",
        "escalation_seconds": WEEK,
    }
    values.update(overrides)

    return evaluate(**values)


# --- when nothing needs escalating ---------------------------------------


def test_a_task_that_is_merely_approaching_its_deadline_is_not_escalated() -> None:
    """Escalating these would make escalation mean nothing."""

    assert _verdict(overdue_seconds=-86400.0).required is False


@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.CANCELLED])
def test_finished_work_is_never_escalated(status: TaskStatus) -> None:
    """However late it once was. A cancelled task is not an outstanding
    request, and a completed one is not a problem."""

    verdict = _verdict(
        status=status,
        is_overdue=True,
        overdue_seconds=100 * WEEK,
        escalation_requested=True,
        priority=TaskPriority.URGENT,
    )

    assert verdict.required is False


def test_a_task_barely_overdue_at_normal_priority_waits() -> None:
    assert _verdict(is_overdue=True, overdue_seconds=3600.0).required is False


# --- the reasons, in precedence order ------------------------------------


def test_an_explicit_request_outranks_everything() -> None:
    """A person saying "escalate this" is evidence, and it is the most
    specific thing anybody can say about a task."""

    verdict = _verdict(escalation_requested=True, status=TaskStatus.BLOCKED)

    assert verdict.reason == REASON_REQUESTED


def test_blocked_is_escalated_even_before_its_deadline() -> None:
    """Blocked is not waiting, it is stuck. A blocked task with weeks to run
    is exactly what a weekly report exists to surface."""

    verdict = _verdict(status=TaskStatus.BLOCKED)

    assert verdict.required is True
    assert verdict.reason == REASON_BLOCKED


def test_an_overdue_high_priority_task_escalates_immediately() -> None:
    verdict = _verdict(
        priority=TaskPriority.HIGH, is_overdue=True, overdue_seconds=60.0
    )

    assert verdict.reason == REASON_OVERDUE_HIGH


def test_a_long_overdue_task_escalates_on_the_threshold() -> None:
    """Tested at the boundary, which is why `escalation_seconds` is a
    parameter rather than a settings lookup."""

    assert _verdict(is_overdue=True, overdue_seconds=WEEK).reason == REASON_LONG_OVERDUE
    assert _verdict(is_overdue=True, overdue_seconds=WEEK - 1).required is False


def test_an_overdue_task_from_a_message_is_an_unanswered_request() -> None:
    """The evidence is exactly that a task derived from an inbound message
    passed its date untouched."""

    verdict = _verdict(
        is_overdue=True, overdue_seconds=3600.0, source="email_follow_up"
    )

    assert verdict.reason == REASON_UNANSWERED


def test_a_manual_task_slightly_overdue_is_not_an_unanswered_request() -> None:
    """Nobody sent it. There is no counterparty who has failed to reply."""

    assert _verdict(is_overdue=True, overdue_seconds=3600.0).required is False


# --- the target ----------------------------------------------------------


def test_a_configured_target_is_used() -> None:
    verdict = _verdict(
        status=TaskStatus.BLOCKED,
        configured_target_name="Sudha P",
        configured_target_address="sudha@sunradia.com",
    )

    assert verdict.target_address == "sudha@sunradia.com"
    assert verdict.target_source is TargetSource.CONFIGURED
    assert verdict.target_display == "Sudha P <sudha@sunradia.com>"


def test_with_nothing_configured_the_target_is_stated_as_unidentified() -> None:
    """A sentence, not an empty field. The report has to read as "nobody is
    set up for this", which is actionable, rather than as a rendering bug."""

    verdict = _verdict(status=TaskStatus.BLOCKED)

    assert verdict.target_address is None
    assert verdict.target_source is TargetSource.NOT_IDENTIFIED
    assert verdict.target_display == NO_TARGET_IDENTIFIED


def test_the_counterparty_is_never_used_as_the_target() -> None:
    """They are usually the person who has not replied. Escalating to them is
    just another follow-up, dressed up — and the report would be telling
    somebody to chase the person they are already chasing."""

    verdict = _verdict(
        status=TaskStatus.BLOCKED,
        contact_name="Robert Keenan",
        contact_address="Robert.Keenan@sunradia.com",
    )

    assert verdict.contact_address == "Robert.Keenan@sunradia.com"
    assert verdict.target_address is None
    assert verdict.target_display == NO_TARGET_IDENTIFIED


def test_a_blank_configured_target_is_treated_as_none() -> None:
    """Whitespace in an environment variable is the ordinary way this gets set
    to nothing, and it must not produce an address of `" "`."""

    verdict = _verdict(status=TaskStatus.BLOCKED, configured_target_address="   ")

    assert verdict.target_source is TargetSource.NOT_IDENTIFIED


# --- the recommended action ----------------------------------------------


def test_the_action_is_something_a_person_does() -> None:
    """Nothing in this system sends an escalation on its own, and the wording
    must never imply otherwise."""

    verdict = _verdict(
        is_overdue=True,
        overdue_seconds=WEEK,
        configured_target_address="sudha@sunradia.com",
    )

    assert verdict.recommended_action == "Prepare an escalation email for review."
    assert "sent" not in (verdict.recommended_action or "")


def test_a_blocked_task_is_asked_about_rather_than_chased() -> None:
    action = _verdict(status=TaskStatus.BLOCKED).recommended_action or ""

    assert "blocked on" in action


def test_with_no_target_the_action_falls_back_to_the_counterparty() -> None:
    verdict = _verdict(
        is_overdue=True,
        overdue_seconds=WEEK,
        contact_address="Robert.Keenan@sunradia.com",
    )

    assert "Robert.Keenan@sunradia.com" in (verdict.recommended_action or "")


def test_with_neither_the_action_is_still_a_real_instruction() -> None:
    action = _verdict(is_overdue=True, overdue_seconds=WEEK).recommended_action or ""

    assert "chase it or close it" in action
