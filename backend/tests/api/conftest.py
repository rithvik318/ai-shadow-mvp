from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.main import app
from tests.support.database import db_session  # noqa: F401
from tests.support.embeddings import fake_embeddings  # noqa: F401

__all__ = ["client", "db_session", "fake_embeddings"]


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """A TestClient whose requests share the test's in-memory session."""

    def override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
