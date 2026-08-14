"""Memory is what the Shadow knows that no document says.

The assertions that matter most are about what does *not* reach a prompt: a
retired memory, an expired one, and everything past the cap.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import MemoryNotFoundError
from app.models.digital_twin import MemoryType
from app.services.features.digital_twin.memory_service import (
    active_memories,
    create_memory,
    deactivate_memory,
    delete_memory,
    list_memories,
    update_memory,
)


def _seed(db: Session, **overrides) -> object:
    values = {
        "memory_type": MemoryType.FACT,
        "content": "SunRadia has delivered MDM for large financial institutions.",
        "importance": 3,
    }
    values.update(overrides)

    return create_memory(db, **values)


# --- creating -------------------------------------------------------------


def test_a_fact_can_be_stored(db_session: Session) -> None:
    memory = _seed(db_session)

    assert memory.type is MemoryType.FACT
    assert memory.active is True
    assert memory.source == "user"


def test_a_preference_can_be_stored(db_session: Session) -> None:
    memory = _seed(
        db_session,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers concise executive-facing emails.",
        importance=4,
    )

    assert memory.type is MemoryType.PREFERENCE
    assert memory.importance == 4


def test_content_whitespace_is_trimmed(db_session: Session) -> None:
    assert _seed(db_session, content="  spaced  ").content == "spaced"


# --- listing --------------------------------------------------------------


def test_memories_are_listed(db_session: Session) -> None:
    _seed(db_session)
    _seed(db_session, memory_type=MemoryType.DECISION, content="Prioritize govt.")

    assert len(list_memories(db_session)) == 2


def test_memories_can_be_filtered_by_type(db_session: Session) -> None:
    _seed(db_session)
    _seed(db_session, memory_type=MemoryType.DECISION, content="Prioritize govt.")

    decisions = list_memories(db_session, memory_type=MemoryType.DECISION)

    assert [memory.content for memory in decisions] == ["Prioritize govt."]


def test_listing_includes_retired_memories_by_default(db_session: Session) -> None:
    """The management view shows everything; only chat filters. A decision that
    no longer applies is still a thing that was decided."""

    memory = _seed(db_session)
    deactivate_memory(db_session, memory.id)

    assert len(list_memories(db_session)) == 1
    assert list_memories(db_session, active=True) == []
    assert len(list_memories(db_session, active=False)) == 1


def test_listing_orders_by_importance(db_session: Session) -> None:
    _seed(db_session, content="low", importance=1)
    _seed(db_session, content="high", importance=5)
    _seed(db_session, content="middling", importance=3)

    assert [memory.content for memory in list_memories(db_session)] == [
        "high",
        "middling",
        "low",
    ]


# --- changing -------------------------------------------------------------


def test_a_memory_can_be_updated(db_session: Session) -> None:
    memory = _seed(db_session)

    updated = update_memory(
        db_session, memory.id, {"content": "Revised.", "importance": 5}
    )

    assert updated.content == "Revised."
    assert updated.importance == 5
    assert updated.type is MemoryType.FACT


def test_updating_an_unknown_memory_raises(db_session: Session) -> None:
    with pytest.raises(MemoryNotFoundError):
        update_memory(db_session, uuid.uuid4(), {"content": "x"})


def test_a_memory_can_be_retired(db_session: Session) -> None:
    memory = _seed(db_session)

    assert deactivate_memory(db_session, memory.id).active is False


def test_a_memory_can_be_deleted(db_session: Session) -> None:
    memory = _seed(db_session)

    delete_memory(db_session, memory.id)

    assert list_memories(db_session) == []


def test_deleting_an_unknown_memory_raises(db_session: Session) -> None:
    with pytest.raises(MemoryNotFoundError):
        delete_memory(db_session, uuid.uuid4())


# --- what reaches a prompt ------------------------------------------------


def test_active_memories_exclude_retired_ones(db_session: Session) -> None:
    """A retired memory must not keep shaping answers — that is the whole
    point of retiring it rather than deleting it."""

    kept = _seed(db_session, content="still true")
    retired = _seed(db_session, content="no longer true")
    deactivate_memory(db_session, retired.id)

    assert [memory.content for memory in active_memories(db_session)] == ["still true"]
    assert kept.active is True


def test_active_memories_exclude_expired_ones(db_session: Session) -> None:
    """An expiry that is not enforced is a comment."""

    _seed(db_session, content="current")
    _seed(
        db_session,
        content="stale",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert [memory.content for memory in active_memories(db_session)] == ["current"]


def test_a_future_expiry_does_not_exclude(db_session: Session) -> None:
    _seed(
        db_session,
        content="current",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    assert len(active_memories(db_session)) == 1


def test_active_memories_are_ordered_by_importance(db_session: Session) -> None:
    _seed(db_session, content="low", importance=1)
    _seed(db_session, content="high", importance=5)

    assert [memory.content for memory in active_memories(db_session)] == ["high", "low"]


def test_active_memories_are_capped(db_session: Session) -> None:
    """The prompt has a budget, and an uncapped list is what spends it."""

    for index in range(12):
        _seed(db_session, content=f"memory {index}", importance=3)

    assert len(active_memories(db_session, limit=8)) == 8


def test_the_cap_keeps_the_most_important(db_session: Session) -> None:
    """Dropping the least important is the only defensible way to lose one."""

    _seed(db_session, content="critical", importance=5)
    for index in range(10):
        _seed(db_session, content=f"routine {index}", importance=1)

    kept = active_memories(db_session, limit=3)

    assert kept[0].content == "critical"
    assert len(kept) == 3


def test_the_cap_defaults_to_the_configured_maximum(db_session: Session) -> None:
    from app.config.settings import settings

    for index in range(settings.MAX_MEMORIES_IN_CONTEXT + 5):
        _seed(db_session, content=f"memory {index}")

    assert len(active_memories(db_session)) == settings.MAX_MEMORIES_IN_CONTEXT
