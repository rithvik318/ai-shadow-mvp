"""When a report covers, decided arithmetically and never by "the last 7 days".

A stored report is only comparable to another one if both cover a period that
somebody else can recompute. "The seven days before I pressed the button" is
not that: two people pressing on the same afternoon get two different windows,
the same period is never generated twice identically, and there is no key that
means "the week of the 3rd".

So a period here is a **calendar** period, in UTC, with half-open bounds:

    start <= received_at < end

Half-open because the alternative — inclusive at both ends — puts a message
that arrives exactly at midnight in two periods, and a digest that
double-counts is worse than one that is a microsecond short.

Weeks begin Monday, matching ISO 8601 and the working week the report is read
against. Months are calendar months. Both are computed from the clock alone —
no database, no settings, no locale — which is what lets the scheduler ask
"has the period ending at X been recorded yet?" and get the same answer as a
person pressing Generate.

UTC throughout, deliberately, and it is a real tradeoff: somebody in IST sees a
week that starts at 05:30 their time. The alternative is a per-user timezone
that changes what a stored period *means* when the user edits it, which would
make historical reports retroactively cover different days. A fixed reference
frame is the one property that keeps history honest, so it wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum


class PeriodKind(StrEnum):
    """The rhythms reports are generated on.

    `RANGE` is the open one and exists because the other two cannot express a
    period that has not finished. "September so far" is neither a week nor a
    month: it starts where a month starts and ends today, and forcing it into
    `MONTH` would label a partial period as a complete one — which is the
    difference between "the month was quiet" and "the month is not over".
    """

    WEEK = "week"
    MONTH = "month"
    RANGE = "range"


@dataclass(frozen=True, order=True)
class Period:
    """A half-open window, `start` inclusive and `end` exclusive."""

    kind: PeriodKind
    start: datetime
    end: datetime

    def contains(self, moment: datetime | None) -> bool:
        """Whether a timestamp falls in this window. None never does."""

        if moment is None:
            return False

        return self.start <= _as_utc(moment) < self.end

    @property
    def label(self) -> str:
        """How the period is named to a person.

        The last *included* day is named, not `end` — a month's window ends at
        midnight on the 1st of the next month, and labelling it with that date
        would put "September" on the August report.
        """

        last = (self.end - timedelta(seconds=1)).date()

        if self.kind is PeriodKind.MONTH:
            return self.start.strftime("%B %Y")

        # `%d` and an explicit lstrip rather than the `%-d` / `%#d` pair: the
        # no-padding flag is spelled differently on Windows and glibc, and this
        # project is developed on one and deployed on the other.
        first_day = f"{self.start:%d}".lstrip("0")
        last_day = f"{last:%d}".lstrip("0")

        return f"{first_day} {self.start:%b} – {last_day} {last:%b %Y}"

    @property
    def is_partial(self) -> bool:
        """Whether this window stops short of the calendar period it sits in.

        True for "1–13 September" and false for "September". A report must say
        which it is: a reader who takes a month-to-date figure for a whole
        month draws a conclusion the data does not support, and no amount of
        detail elsewhere in the document corrects that.
        """

        if self.kind is not PeriodKind.RANGE:
            return False

        # Bounds only, never `kind`: a range covering exactly one calendar
        # month is that month, and comparing the dataclasses would call it
        # partial purely because one carries RANGE and the other MONTH.
        enclosing = month_of(self.start)

        if (self.start, self.end) == (enclosing.start, enclosing.end):
            return False

        week = week_of(self.start)

        return (self.start, self.end) != (week.start, week.end)

    @property
    def days(self) -> int:
        """Whole days covered, rounded up. One for anything shorter."""

        return max(1, -(-int((self.end - self.start).total_seconds()) // 86_400))

    @property
    def key(self) -> str:
        """A stable identity for this window, for a URL or a log line.

        Derived from `start`, which is unique per kind, so it round-trips
        through `parse_key` without a lookup.
        """

        if self.kind is PeriodKind.MONTH:
            return f"{self.start:%Y-%m}"

        if self.kind is PeriodKind.RANGE:
            # Both ends, because a range is not identified by its start: two
            # reports can begin on the 1st and cover different windows.
            last = (self.end - timedelta(seconds=1)).date()
            return f"{self.start:%Y-%m-%d}..{last:%Y-%m-%d}"

        return f"{self.start:%Y-%m-%d}"


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _as_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)

    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def week_of(moment: datetime | None = None) -> Period:
    """The Monday-to-Monday window containing `moment`."""

    instant = _as_utc(moment)
    start = _midnight(instant.date() - timedelta(days=instant.weekday()))

    return Period(kind=PeriodKind.WEEK, start=start, end=start + timedelta(days=7))


def month_of(moment: datetime | None = None) -> Period:
    """The calendar month containing `moment`."""

    instant = _as_utc(moment)
    start = _midnight(instant.date().replace(day=1))

    # Adding 32 days from the 1st always lands in the next month whatever its
    # length, and taking that month's 1st avoids a table of month lengths and
    # the February branch that goes with it.
    end = _midnight((start + timedelta(days=32)).date().replace(day=1))

    return Period(kind=PeriodKind.MONTH, start=start, end=end)


def current(kind: PeriodKind, moment: datetime | None = None) -> Period:
    """The in-progress period of this kind.

    A report generated over one of these is a *partial* answer — the period has
    not finished — which is why `Period.end` being in the future is what the
    persistence layer reads to decide a snapshot is provisional.
    """

    return week_of(moment) if kind is PeriodKind.WEEK else month_of(moment)


def previous(kind: PeriodKind, moment: datetime | None = None) -> Period:
    """The most recently *completed* period of this kind.

    What the scheduler generates. A digest is only final once its window has
    closed, so nothing is snapshotted for a period that is still running.
    """

    started = current(kind, moment).start

    return current(kind, started - timedelta(seconds=1))


def preceding(period: Period, count: int) -> list[Period]:
    """The `count` periods immediately before `period`, newest first.

    Used to offer a history selector before any history exists: the periods a
    person can ask about are a function of the calendar, not of what happens to
    be stored.
    """

    windows: list[Period] = []
    cursor = period.start

    for _ in range(max(count, 0)):
        earlier = current(period.kind, cursor - timedelta(seconds=1))
        windows.append(earlier)
        cursor = earlier.start

    return windows


def parse_key(kind: PeriodKind, key: str) -> Period:
    """Rebuild a period from `Period.key`.

    Raises `ValueError` for anything that is not a key this module emits. The
    caller turns that into a 400 — a malformed period is a bad request, not an
    empty report, and answering with the current period instead would silently
    show somebody a different week than the one they asked for.
    """

    text = (key or "").strip()

    if kind is PeriodKind.MONTH:
        anchor = datetime.strptime(text, "%Y-%m").replace(tzinfo=UTC)
        return month_of(anchor)

    anchor = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    week = week_of(anchor)

    if week.key != text:
        raise ValueError(
            f"{key!r} is not the start of a week. Weeks begin on Monday; the "
            f"week containing that date starts {week.key}."
        )

    return week


def range_between(start: datetime, end: datetime) -> Period:
    """An arbitrary window, `start` inclusive and `end` exclusive."""

    first, last = _as_utc(start), _as_utc(end)

    if last <= first:
        raise ValueError("A reporting period must end after it starts.")

    return Period(kind=PeriodKind.RANGE, start=first, end=last)


def to_date(kind: PeriodKind, moment: datetime | None = None) -> Period:
    """The current week or month up to `moment`, and no further.

    What "September so far" resolves to. Deliberately a `RANGE` even though it
    begins where a month begins: the window is not the month, and a report that
    called it one would be describing thirteen days under a heading that
    promises thirty.

    The end is `moment` itself rather than the end of today, so a report
    generated at nine in the morning does not claim to cover the afternoon.
    """

    now = _as_utc(moment)
    enclosing = week_of(now) if kind is PeriodKind.WEEK else month_of(now)

    return Period(kind=PeriodKind.RANGE, start=enclosing.start, end=now)


def split_into_weeks(period: Period) -> list[Period]:
    """Cut a period into consecutive week-long segments, the last one short.

    The month-to-date report's backbone. A thirteen-day window becomes 1–7 and
    8–13, a full September becomes four weeks and a two-day tail, and February
    becomes four exactly — no month is special-cased and nothing is hardcoded.

    Segments are cut from the period's own start rather than snapped to
    Mondays. Snapping would make the first segment of a month report cover days
    before the month began, which is a window the report is not allowed to
    describe. A period of a week or less is one segment: splitting it would
    produce a "week 1" identical to the report it sits in.
    """

    if period.end - period.start <= timedelta(days=7):
        return [period]

    segments: list[Period] = []
    cursor = period.start

    while cursor < period.end:
        segment_end = min(cursor + timedelta(days=7), period.end)
        segments.append(Period(kind=PeriodKind.RANGE, start=cursor, end=segment_end))
        cursor = segment_end

    return segments
