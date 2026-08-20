"""`UtcDateTime`: a timestamp that is aware on every dialect.

`DateTime(timezone=True)` is a request Postgres honours and SQLite ignores.
These tests run on SQLite, which is the dialect that drops the offset — so a
regression here fails loudly rather than passing everywhere except production.

Two properties matter, and the second is the one that would corrupt data:
a value comes back aware, and it comes back as the *same instant* it went in.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.types import UtcDateTime
from app.models.email import EmailAssessment, EmailCategory, EmailPriority
from app.models.user import User

BERLIN = timezone(timedelta(hours=2))


def _store(db: Session, user: User, moment: datetime | None) -> EmailAssessment:
    assessment = EmailAssessment(
        user_id=user.id,
        provider="test",
        provider_message_id="m1",
        category=EmailCategory.FOLLOW_UP,
        priority=EmailPriority.NORMAL,
        summary="Stored to check the column, not the summary.",
        action_items=[],
        follow_up_recommended=True,
        follow_up_due_at=moment,
    )
    db.add(assessment)
    db.commit()
    # Expired and re-read from the database, so this asserts what storage
    # returns rather than what the object was handed.
    db.expire_all()

    return db.execute(select(EmailAssessment)).scalar_one()


def test_a_naive_value_comes_back_aware_and_unshifted(
    db_session: Session, test_user: User
) -> None:
    """The failure this type exists for: SQLite returns a naive datetime, and
    every comparison against `datetime.now(UTC)` then raises TypeError."""

    stored = _store(db_session, test_user, datetime(2026, 8, 21, 17, 0))

    assert stored.follow_up_due_at == datetime(2026, 8, 21, 17, 0, tzinfo=UTC)
    assert stored.follow_up_due_at.tzinfo is not None


def test_an_aware_utc_value_round_trips_unchanged(
    db_session: Session, test_user: User
) -> None:
    moment = datetime(2026, 8, 21, 17, 0, tzinfo=UTC)

    assert _store(db_session, test_user, moment).follow_up_due_at == moment


def test_an_offset_value_keeps_its_instant(
    db_session: Session, test_user: User
) -> None:
    """The silent-corruption case. SQLAlchemy's SQLite bind processor formats a
    datetime from its clock fields and discards `tzinfo`, so 19:00+02:00 would
    be stored as 19:00 and read back as 19:00 UTC — two hours late. Converting
    to UTC before binding is what makes that impossible."""

    moment = datetime(2026, 8, 21, 19, 0, tzinfo=BERLIN)

    stored = _store(db_session, test_user, moment).follow_up_due_at

    assert stored == moment
    assert stored == datetime(2026, 8, 21, 17, 0, tzinfo=UTC)


def test_null_stays_null(db_session: Session, test_user: User) -> None:
    assert _store(db_session, test_user, None).follow_up_due_at is None


def test_a_server_generated_timestamp_is_also_aware(
    db_session: Session, test_user: User
) -> None:
    """`assessed_at` has a server default, so it never passes through the bind
    side. The read side has to label it too."""

    stored = _store(db_session, test_user, None)

    assert stored.assessed_at.tzinfo is not None


def test_a_stored_timestamp_can_be_compared_to_now(
    db_session: Session, test_user: User
) -> None:
    """The whole point, stated as the operation that used to raise.

    Subtracting a naive datetime from an aware one is a `TypeError`, and
    "is this follow-up overdue?" is exactly that subtraction. Before this type,
    the answer depended on which database you were running against."""

    stored = _store(db_session, test_user, datetime(2026, 8, 21, 17, 0))

    assert isinstance(stored.follow_up_due_at - datetime.now(UTC), timedelta)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 8, 21, 17, 0), datetime(2026, 8, 21, 17, 0, tzinfo=UTC)],
)
def test_binding_always_produces_utc(value: datetime) -> None:
    """Checked directly, because the bind side is what protects the offset
    case and SQLite cannot show it back to us."""

    bound = UtcDateTime().process_bind_param(value, None)

    assert bound is not None
    assert bound.utcoffset() == timedelta(0)
