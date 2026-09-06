"""The colour rules, at their exact boundaries.

Every example the milestone gave is here as a named case, because these are the
numbers a person will check the product against. `now` is injected so the
boundaries are tested exactly rather than approximately — a test that says
"about three days" cannot tell a passing implementation from an off-by-one one.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.features.tasks.urgency import (
    DISPLAY_OVERDUE,
    TaskStatus,
    TaskUrgency,
    evaluate,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
WARNING_DAYS = 3


def _verdict(status: TaskStatus, due_at: datetime | None):
    return evaluate(status=status, due_at=due_at, warning_days=WARNING_DAYS, now=NOW)


# --- the milestone's worked examples --------------------------------------


def test_a_completed_task_is_green() -> None:
    verdict = _verdict(TaskStatus.COMPLETED, NOW - timedelta(days=10))

    assert verdict.urgency is TaskUrgency.NORMAL
    assert verdict.display_status == "completed"
    # Completed and once-late is still green: the report is about what needs
    # attention, and this does not.
    assert verdict.is_overdue is False


def test_pending_due_in_one_day_is_amber() -> None:
    assert (
        _verdict(TaskStatus.TODO, NOW + timedelta(days=1)).urgency
        is TaskUrgency.WARNING
    )


def test_pending_due_in_three_days_is_amber() -> None:
    """Exactly on the threshold, and inclusive."""

    assert (
        _verdict(TaskStatus.TODO, NOW + timedelta(days=3)).urgency
        is TaskUrgency.WARNING
    )


def test_pending_due_in_four_days_is_not_urgent() -> None:
    assert (
        _verdict(TaskStatus.TODO, NOW + timedelta(days=4)).urgency is TaskUrgency.NORMAL
    )


def test_overdue_by_one_day_is_red() -> None:
    verdict = _verdict(TaskStatus.TODO, NOW - timedelta(days=1))

    assert verdict.urgency is TaskUrgency.CRITICAL
    assert verdict.display_status == DISPLAY_OVERDUE
    assert verdict.is_overdue is True


def test_overdue_by_four_days_is_still_red() -> None:
    """There is nothing louder than critical, and lateness does not decay."""

    assert (
        _verdict(TaskStatus.TODO, NOW - timedelta(days=4)).urgency
        is TaskUrgency.CRITICAL
    )


def test_a_blocked_task_is_red() -> None:
    verdict = _verdict(TaskStatus.BLOCKED, None)

    assert verdict.urgency is TaskUrgency.CRITICAL
    assert verdict.display_status == "blocked"


# --- the rules those examples imply ---------------------------------------


def test_a_passed_deadline_never_means_completed() -> None:
    """The explicit instruction, asserted rather than assumed."""

    verdict = _verdict(TaskStatus.TODO, NOW - timedelta(days=30))

    assert verdict.status is TaskStatus.TODO
    assert verdict.display_status != "completed"


def test_a_task_with_no_due_date_is_never_urgent_on_time_alone() -> None:
    verdict = _verdict(TaskStatus.TODO, None)

    assert verdict.urgency is TaskUrgency.NORMAL
    assert verdict.is_overdue is False
    # None, not zero: "no deadline" and "due exactly now" are different facts.
    assert verdict.overdue_seconds is None


def test_a_cancelled_task_is_not_urgent() -> None:
    assert (
        _verdict(TaskStatus.CANCELLED, NOW - timedelta(days=30)).urgency
        is TaskUrgency.NORMAL
    )


def test_a_blocked_task_that_is_also_late_stays_critical() -> None:
    verdict = _verdict(TaskStatus.BLOCKED, NOW - timedelta(days=9))

    assert verdict.urgency is TaskUrgency.CRITICAL
    assert verdict.is_overdue is True


def test_the_boundary_is_a_timestamp_not_a_calendar_date() -> None:
    """One second either side of the threshold must classify differently.

    A date-only comparison would put both of these in the same bucket, and the
    answer would then depend on what time of day the report was opened.
    """

    inside = _verdict(TaskStatus.TODO, NOW + timedelta(days=3) - timedelta(seconds=1))
    outside = _verdict(TaskStatus.TODO, NOW + timedelta(days=3, seconds=1))

    assert inside.urgency is TaskUrgency.WARNING
    assert outside.urgency is TaskUrgency.NORMAL


def test_a_naive_due_date_is_read_as_utc_rather_than_raising() -> None:
    """Mixing aware and naive datetimes raises; that must not reach a report."""

    verdict = evaluate(
        status=TaskStatus.TODO,
        due_at=datetime(2026, 8, 30, 12, 0),
        warning_days=WARNING_DAYS,
        now=NOW,
    )

    assert verdict.is_overdue is True


@pytest.mark.parametrize("days", [0, 1, 7])
def test_the_threshold_is_configurable(days: int) -> None:
    verdict = evaluate(
        status=TaskStatus.TODO,
        due_at=NOW + timedelta(days=5),
        warning_days=days,
        now=NOW,
    )

    expected = TaskUrgency.WARNING if days >= 5 else TaskUrgency.NORMAL

    assert verdict.urgency is expected
