from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model.

    Alembic's `target_metadata` points at `Base.metadata`, so any model that
    should be picked up by autogenerate must be imported in
    `app/models/__init__.py`.
    """
