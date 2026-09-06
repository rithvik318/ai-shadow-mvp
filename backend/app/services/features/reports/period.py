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
    """The two rhythms reports are generated on."""

    WEEK = "week"
    MONTH = "month"


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
    def key(self) -> str:
        """A stable identity for this window, for a URL or a log line.

        Derived from `start`, which is unique per kind, so it round-trips
        through `parse_key` without a lookup.
        """

        if self.kind is PeriodKind.MONTH:
            return f"{self.start:%Y-%m}"

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
