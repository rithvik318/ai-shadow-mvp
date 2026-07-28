"""Shared in-memory database fixtures.

Kept out of the root `conftest.py` on purpose: parser and chunker tests are
pure functions over bytes and should not need database packages imported to
collect.

SQLite is used rather than Postgres so the suite runs with no services
present. The `embedding` column is declared with a JSON variant for SQLite
(see `app/models/document.py`), so schema creation succeeds here while
production keeps the real pgvector type.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.database.base import Base


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

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()
