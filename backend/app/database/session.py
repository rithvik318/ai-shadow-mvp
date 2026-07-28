from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database.database import SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped database session."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
