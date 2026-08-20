"""A `DateTime` column that is timezone-aware on every dialect.

`DateTime(timezone=True)` is a request, not a guarantee. Postgres honours it and
returns aware datetimes. **SQLite ignores it**: its `DATETIME` storage format
carries no offset, so a value written as `17:00+00:00` comes back as a naive
`17:00`. The test suite runs on SQLite, so without this every timestamp the
suite reads is a different type from the one production reads — and the tests
that matter most about time are exactly the ones that would not notice.

Worse than the type mismatch is the arithmetic. SQLAlchemy's SQLite bind
processor formats a datetime from its year/month/day/hour fields and **discards
`tzinfo` entirely**, so a value carrying `+02:00` would be stored as its local
wall clock and read back as though it were UTC — a silent two-hour shift. This
decorator converts to UTC *before* binding, which is what makes that impossible.

Both directions preserve the instant:

- **binding** — naive is taken as UTC and labelled; aware is converted to UTC.
- **reading** — naive is labelled UTC (which is what was stored); aware is
  normalised to UTC.

Nothing above this module has to know which database it is talking to, which is
the same bargain `app/database/vector.py` strikes for cosine distance.

Applied to the email tables. The others predate it and are deliberately left
alone: retrofitting them is a behaviour change to working code that this task
did not require, and it belongs in its own reviewable commit.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A timestamp that is always read back as an aware UTC datetime."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None:
            # A naive value reaching the database is taken as UTC rather than
            # rejected: every naive datetime this application produces is UTC,
            # and refusing one here would turn a harmless omission into a 500.
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None

        if not isinstance(value, datetime):
            return value

        if value.tzinfo is None:
            # SQLite, and any other dialect that drops the offset. What was
            # stored was UTC, so this labels it rather than converting it —
            # the clock reading is unchanged.
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)
