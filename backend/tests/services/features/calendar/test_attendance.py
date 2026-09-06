"""Whether a meeting happened, and the refusal to guess.

Pure-function tests, so every boundary is exact rather than approximate. The
one that matters most is the last section: a past meeting nobody has answered
for reads as `unknown`, not as attended and not as missed.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.features.calendar.attendance import (
    SETTABLE_STATUSES,
    EventStatus,
    evaluate,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _verdict(
    status: EventStatus,
    starts_at: datetime,
    ends_at: datetime | None = None,
):
    return evaluate(status=status, starts_at=starts_at, ends_at=ends_at, now=NOW)


# --- a meeting that has not happened yet ---------------------------------


def test_a_future_meeting_reads_as_scheduled() -> None:
    verdict = _verdict(EventStatus.SCHEDULED, NOW + timedelta(days=1))

    assert verdict.display_status is EventStatus.SCHEDULED
    assert verdict.is_past is False
    assert verdict.needs_answer is False


def test_a_future_meeting_is_never_asked_about() -> None:
    """`unknown` on a meeting that has not happened is not a question worth
    putting in front of somebody."""

    verdict = _verdict(EventStatus.UNKNOWN, NOW + timedelta(days=2))

    assert verdict.display_status is EventStatus.SCHEDULED
    assert verdict.needs_answer is False


def test_a_meeting_in_progress_is_not_yet_past() -> None:
    verdict = _verdict(
        EventStatus.SCHEDULED, NOW - timedelta(minutes=10), NOW + timedelta(minutes=50)
    )

    assert verdict.is_past is False


# --- the refusal ---------------------------------------------------------


def test_a_finished_meeting_nobody_answered_for_is_unknown() -> None:
    """The whole point of the module.

    Graph containing a meeting is evidence that somebody scheduled it, not
    that anybody went. Reading this as `attended` would launder an assumption
    into a record, and the person reading the report would have no way to tell
    which rows are observed and which are guessed.
    """

    verdict = _verdict(EventStatus.SCHEDULED, NOW - timedelta(days=1))

    assert verdict.status is EventStatus.SCHEDULED
    assert verdict.display_status is EventStatus.UNKNOWN
    assert verdict.needs_answer is True


def test_the_stored_status_is_not_rewritten_by_the_reading() -> None:
    """`scheduled` stays in the row. Sweeping it to `unknown` would need a
    background job, and would be wrong for the window between the meeting
    ending and the sweep running."""

    assert _verdict(EventStatus.SCHEDULED, NOW - timedelta(days=3)).status is (
        EventStatus.SCHEDULED
    )


def test_an_event_with_no_end_is_past_once_it_has_started() -> None:
    """Treated as instantaneous rather than as running forever — the
    alternative is a meeting that can never be asked about."""

    assert _verdict(EventStatus.SCHEDULED, NOW - timedelta(seconds=1)).is_past is True


def test_an_event_ending_exactly_now_is_past() -> None:
    assert _verdict(EventStatus.SCHEDULED, NOW - timedelta(hours=1), NOW).is_past


def test_a_naive_timestamp_is_read_as_utc_rather_than_guessed() -> None:
    """Comparing an aware and a naive datetime raises, and the failure would
    surface as an unrelated 500 in a report."""

    verdict = _verdict(EventStatus.SCHEDULED, datetime(2026, 8, 30, 9, 0))

    assert verdict.is_past is True


# --- an answered meeting -------------------------------------------------


@pytest.mark.parametrize("status", [EventStatus.ATTENDED, EventStatus.MISSED])
def test_an_answered_meeting_is_not_asked_about_again(status: EventStatus) -> None:
    verdict = _verdict(status, NOW - timedelta(days=1))

    assert verdict.display_status is status
    assert verdict.needs_answer is False


def test_an_answer_survives_on_a_future_meeting() -> None:
    """Somebody may know in advance that they will not be there."""

    verdict = _verdict(EventStatus.MISSED, NOW + timedelta(days=1))

    assert verdict.display_status is EventStatus.MISSED


def test_a_cancelled_meeting_is_never_asked_about() -> None:
    """It did not happen, for everybody. There is nothing to ask."""

    verdict = _verdict(EventStatus.CANCELLED, NOW - timedelta(days=1))

    assert verdict.display_status is EventStatus.CANCELLED
    assert verdict.needs_answer is False


def test_every_status_a_person_may_set_is_one_the_domain_knows() -> None:
    assert SETTABLE_STATUSES <= set(EventStatus)
    # `unknown` is settable on purpose: it withdraws an answer.
    assert EventStatus.UNKNOWN in SETTABLE_STATUSES
