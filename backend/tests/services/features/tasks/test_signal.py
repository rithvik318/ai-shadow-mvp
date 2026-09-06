"""The four colours, and the boundary between amber and red.

Pure functions over a clock, so these need no database. The boundary cases are
written out one by one because that is where a colour rule goes wrong: not in
the obvious middle of a band, but at the hour on either side of the edge.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.features.tasks.signal import (
    ATTENTION_AFTER_DAYS,
    TaskSignal,
    days_overdue,
    signal_for,
)
from app.services.features.tasks.urgency import TaskStatus

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def due(days: float) -> datetime:
    """A deadline `days` in the future. Negative is that far in the past."""

    return NOW + timedelta(days=days)


def signal(days: float | None, status: TaskStatus = TaskStatus.TODO) -> TaskSignal:
    return signal_for(
        status=status, due_at=None if days is None else due(days), now=NOW
    )


class TestTheBands:
    def test_a_deadline_that_has_not_passed_is_grey(self):
        assert signal(5) is TaskSignal.TODO
        assert signal(0.5) is TaskSignal.TODO

    def test_a_task_with_no_deadline_is_grey_forever(self):
        # Nothing was ever promised about when it would be finished. Inventing
        # a deadline to have something to colour is the one thing this does not
        # do.
        assert signal(None) is TaskSignal.TODO

    def test_less_than_two_days_late_is_still_grey(self):
        # A deadline missed this morning is not yet a problem worth colouring.
        assert signal(-0.1) is TaskSignal.TODO
        assert signal(-1) is TaskSignal.TODO
        assert signal(-1.99) is TaskSignal.TODO

    def test_exactly_two_days_late_is_amber(self):
        assert signal(-2) is TaskSignal.ATTENTION

    def test_the_whole_of_the_third_day_is_amber(self):
        # Whole days, not an instant. A task two days and six hours late reads
        # as "2 days overdue" on the screen and should be amber for all of that
        # day rather than for the moment it crosses forty-eight hours.
        assert signal(-2.01) is TaskSignal.ATTENTION
        assert signal(-2.5) is TaskSignal.ATTENTION
        assert signal(-2.99) is TaskSignal.ATTENTION

    def test_more_than_two_days_late_is_red(self):
        assert signal(-3) is TaskSignal.URGENT
        assert signal(-10) is TaskSignal.URGENT

    def test_the_amber_band_is_exactly_one_day_wide(self):
        # Stated as an invariant rather than as three examples, so a future
        # change to the threshold cannot quietly widen or close the band.
        just_before = signal(-(ATTENTION_AFTER_DAYS - 0.01))
        at_the_edge = signal(-ATTENTION_AFTER_DAYS)
        just_after = signal(-(ATTENTION_AFTER_DAYS + 1))

        assert (just_before, at_the_edge, just_after) == (
            TaskSignal.TODO,
            TaskSignal.ATTENTION,
            TaskSignal.URGENT,
        )


class TestFinishedWork:
    def test_a_completed_task_is_green_however_late_it_was(self):
        # The page is about what needs attention. Work that is finished does
        # not, and colouring it red would tell somebody to act on something
        # they already did.
        assert signal(-30, TaskStatus.COMPLETED) is TaskSignal.DONE

    def test_a_cancelled_task_shares_the_finished_signal(self):
        # Neither needs attention, which is the only question the signal
        # answers. Abandoned and achieved are still different, and `status`
        # carries that difference for the label.
        assert signal(-30, TaskStatus.CANCELLED) is TaskSignal.DONE

    @pytest.mark.parametrize(
        "status", [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]
    )
    def test_every_open_status_is_coloured_by_its_deadline_alone(self, status):
        # Blocked is a reason, not a temperature. A blocked task due next month
        # is not urgent, and an in-progress one four days late is.
        assert signal(5, status) is TaskSignal.TODO
        assert signal(-4, status) is TaskSignal.URGENT


class TestDaysOverdue:
    def test_it_counts_whole_days_only(self):
        assert days_overdue(due(-2.9), NOW) == 2
        assert days_overdue(due(-3.0), NOW) == 3

    def test_a_task_that_is_not_late_is_zero_rather_than_negative(self):
        # "Minus six days overdue" invites a caller to compare it against a
        # threshold and colour a task that is perfectly fine.
        assert days_overdue(due(6), NOW) == 0
        assert days_overdue(due(0), NOW) == 0

    def test_no_deadline_is_none_rather_than_zero(self):
        # Different facts: nothing is due, versus something is due and is not
        # yet late.
        assert days_overdue(None, NOW) is None

    def test_a_naive_deadline_is_read_as_utc_rather_than_rejected(self):
        assert days_overdue(datetime(2026, 9, 7, 12, 0), NOW) == 3
