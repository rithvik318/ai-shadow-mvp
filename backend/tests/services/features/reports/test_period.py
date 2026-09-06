"""The calendar arithmetic every stored report is keyed on.

Pure functions over the clock, so these tests need no database and no fixtures.
They are worth having in full because everything else in the reports feature
trusts them: the unique constraint keys on `period_start`, so two callers
disagreeing about when a week begins would produce two rows for one week.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.features.reports.period import (
    Period,
    PeriodKind,
    current,
    month_of,
    parse_key,
    preceding,
    previous,
    week_of,
)

# A Wednesday.
WEDNESDAY = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)


class TestWeeks:
    def test_a_week_starts_on_the_monday_before(self):
        week = week_of(WEDNESDAY)

        assert week.start == datetime(2026, 8, 31, tzinfo=UTC)
        assert week.end == datetime(2026, 9, 7, tzinfo=UTC)

    def test_monday_midnight_belongs_to_the_week_it_opens(self):
        # The half-open bound, from the near side. A message arriving exactly
        # at the boundary must land in exactly one week.
        boundary = datetime(2026, 8, 31, tzinfo=UTC)

        assert week_of(boundary).start == boundary

    def test_a_week_excludes_the_instant_it_ends(self):
        week = week_of(WEDNESDAY)

        assert week.contains(week.end - timedelta(microseconds=1))
        assert not week.contains(week.end)

    def test_consecutive_weeks_neither_overlap_nor_leave_a_gap(self):
        week = week_of(WEDNESDAY)
        following = week_of(week.end)

        assert following.start == week.end

    def test_a_naive_timestamp_is_read_as_utc_rather_than_rejected(self):
        # Every naive datetime this application produces is UTC. Refusing one
        # here would turn a harmless omission into a 500 inside a report.
        assert week_of(datetime(2026, 9, 2, 14, 30)) == week_of(WEDNESDAY)

    def test_a_timestamp_in_another_zone_is_converted_not_truncated(self):
        # 01:00 on Monday in UTC+05:30 is still Sunday in UTC, so it belongs to
        # the *previous* week. Reading the wall clock instead of the instant is
        # exactly the bug this asserts against.
        ist = timezone(timedelta(hours=5, minutes=30))
        moment = datetime(2026, 8, 31, 1, 0, tzinfo=ist)

        assert week_of(moment).start == datetime(2026, 8, 24, tzinfo=UTC)


class TestMonths:
    def test_a_month_runs_from_the_first_to_the_next_first(self):
        month = month_of(WEDNESDAY)

        assert month.start == datetime(2026, 9, 1, tzinfo=UTC)
        assert month.end == datetime(2026, 10, 1, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("moment", "end"),
        [
            (datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
            (datetime(2026, 2, 15, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)),
            # A leap February, which is where a fixed 30- or 31-day step breaks.
            (datetime(2024, 2, 15, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC)),
            (datetime(2026, 12, 15, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        ],
    )
    def test_every_month_length_lands_on_the_next_first(self, moment, end):
        assert month_of(moment).end == end


class TestNaming:
    def test_a_month_is_labelled_by_its_own_name_not_the_next_one(self):
        # The window ends at midnight on 1 October; labelling from `end` would
        # put "October" on the September report.
        assert month_of(WEDNESDAY).label == "September 2026"

    def test_a_week_is_labelled_by_its_last_included_day(self):
        assert week_of(WEDNESDAY).label == "31 Aug – 6 Sep 2026"

    def test_a_key_round_trips_through_parsing(self):
        for period in (week_of(WEDNESDAY), month_of(WEDNESDAY)):
            assert parse_key(period.kind, period.key) == period

    def test_a_week_key_that_is_not_a_monday_is_refused(self):
        # Silently correcting it would answer a different question from the one
        # asked, and the caller would have no way to tell.
        with pytest.raises(ValueError, match="Monday"):
            parse_key(PeriodKind.WEEK, "2026-09-02")

    def test_nonsense_is_refused_rather_than_defaulted(self):
        with pytest.raises(ValueError):
            parse_key(PeriodKind.WEEK, "last week")

        with pytest.raises(ValueError):
            parse_key(PeriodKind.MONTH, "")


class TestSelection:
    def test_previous_is_the_last_period_that_has_actually_closed(self):
        assert previous(PeriodKind.WEEK, WEDNESDAY) == week_of(
            datetime(2026, 8, 26, tzinfo=UTC)
        )
        assert previous(PeriodKind.MONTH, WEDNESDAY) == month_of(
            datetime(2026, 8, 15, tzinfo=UTC)
        )

    def test_previous_never_returns_a_period_still_running(self):
        for kind in PeriodKind:
            assert previous(kind, WEDNESDAY).end <= WEDNESDAY

    def test_the_selector_walks_back_without_repeating_or_skipping(self):
        running = current(PeriodKind.WEEK, WEDNESDAY)
        earlier = preceding(running, 4)

        assert len(earlier) == 4
        assert len({period.key for period in earlier}) == 4

        for later, sooner in zip([running, *earlier[:-1]], earlier, strict=True):
            assert sooner.end == later.start

    def test_asking_for_no_history_gives_none(self):
        assert preceding(current(PeriodKind.MONTH, WEDNESDAY), 0) == []


class TestContains:
    def test_nothing_contains_a_missing_timestamp(self):
        # A message the provider returned with no date belongs to no period.
        # Sweeping it into whichever window is being generated would invent a
        # date nobody supplied.
        assert not week_of(WEDNESDAY).contains(None)

    def test_a_period_is_ordered_by_its_start(self):
        older = week_of(datetime(2026, 8, 1, tzinfo=UTC))
        newer = week_of(WEDNESDAY)

        assert sorted([newer, older]) == [older, newer]

    def test_two_periods_over_the_same_window_are_equal(self):
        # Frozen and comparable by value, which is what lets the store look a
        # period up without carrying an identity around.
        assert week_of(WEDNESDAY) == Period(
            kind=PeriodKind.WEEK,
            start=datetime(2026, 8, 31, tzinfo=UTC),
            end=datetime(2026, 9, 7, tzinfo=UTC),
        )
