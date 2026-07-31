"""Shared in-memory database fixtures.

Kept out of the root `conftest.py` on purpose: parser and chunker tests are
pure functions over bytes and should not need database packages imported to
collect.

SQLite is used rather than Postgres so the suite runs with no services
present. The `embedding` column is declared with a JSON variant for SQLite
(see `app/models/document.py`), so schema creation succeeds here while
production keeps the real pgvector type.
"""

import json
import math
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.database.base import Base


def sqlite_cosine_distance(left: str | None, right: str | None) -> float | None:
    """SQLite stand-in for pgvector's `<=>` operator.

    Registered as a SQL function so `app/database/vector.py` compiles to a real
    call here, and the ordering, threshold and limiting logic under test is the
    same logic production runs — rather than a Python reimplementation that
    could drift from it.

    Returns NULL where pgvector's schema would have refused the row outright: a
    vector of the wrong width, or one of zero length whose direction is
    undefined. `retrieval_service` filters those out explicitly, because SQLite
    and Postgres sort NULLs to opposite ends.
    """

    if left is None or right is None:
        return None

    a = json.loads(left)
    b = json.loads(right)

    if not a or len(a) != len(b):
        return None

    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return None

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return 1.0 - (dot / (norm_a * norm_b))


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Yield a session against a fresh, isolated in-memory database."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        # SQLite ignores ON DELETE CASCADE unless this is switched on, which
        # would silently hide chunk-cascade regressions.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

        dbapi_connection.create_function("cosine_distance", 2, sqlite_cosine_distance)

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()
