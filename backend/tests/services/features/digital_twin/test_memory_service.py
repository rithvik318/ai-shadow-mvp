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
from app.models.user import User
from app.services.features.digital_twin.memory_service import (
    active_memories,
    create_memory,
    deactivate_memory,
    delete_memory,
    list_memories,
    update_memory,
)


def _seed(db: Session, user: User, **overrides) -> object:
    values = {
        "memory_type": MemoryType.FACT,
        "content": "SunRadia has delivered MDM for large financial institutions.",
        "importance": 3,
        "user_id": user.id,
    }
    values.update(overrides)

    return create_memory(db, **values)


# --- creating -------------------------------------------------------------


def test_a_fact_can_be_stored(db_session: Session, test_user: User) -> None:
    memory = _seed(db_session, test_user)

    assert memory.type is MemoryType.FACT
    assert memory.active is True
    assert memory.source == "user"


def test_a_preference_can_be_stored(db_session: Session, test_user: User) -> None:
    memory = _seed(
        db_session,
        test_user,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers concise executive-facing emails.",
        importance=4,
    )

    assert memory.type is MemoryType.PREFERENCE
    assert memory.importance == 4


def test_content_whitespace_is_trimmed(db_session: Session, test_user: User) -> None:
    assert _seed(db_session, test_user, content="  spaced  ").content == "spaced"


# --- listing --------------------------------------------------------------


def test_memories_are_listed(db_session: Session, test_user: User) -> None:
    _seed(db_session, test_user)
    _seed(
        db_session,
        test_user,
        memory_type=MemoryType.DECISION,
        content="Prioritize govt.",
    )

    assert len(list_memories(db_session, user_id=test_user.id)) == 2


def test_memories_can_be_filtered_by_type(db_session: Session, test_user: User) -> None:
    _seed(db_session, test_user)
    _seed(
        db_session,
        test_user,
        memory_type=MemoryType.DECISION,
        content="Prioritize govt.",
    )

    decisions = list_memories(
        db_session, memory_type=MemoryType.DECISION, user_id=test_user.id
    )

    assert [memory.content for memory in decisions] == ["Prioritize govt."]


def test_listing_includes_retired_memories_by_default(
    db_session: Session, test_user: User
) -> None:
    """The management view shows everything; only chat filters. A decision that
    no longer applies is still a thing that was decided."""

    memory = _seed(db_session, test_user)
    deactivate_memory(db_session, memory.id, user_id=test_user.id)

    assert len(list_memories(db_session, user_id=test_user.id)) == 1
    assert list_memories(db_session, active=True, user_id=test_user.id) == []
    assert len(list_memories(db_session, active=False, user_id=test_user.id)) == 1


def test_listing_orders_by_importance(db_session: Session, test_user: User) -> None:
    _seed(db_session, test_user, content="low", importance=1)
    _seed(db_session, test_user, content="high", importance=5)
    _seed(db_session, test_user, content="middling", importance=3)

    assert [
        memory.content for memory in list_memories(db_session, user_id=test_user.id)
    ] == [
        "high",
        "middling",
        "low",
    ]


# --- changing -------------------------------------------------------------


def test_a_memory_can_be_updated(db_session: Session, test_user: User) -> None:
    memory = _seed(db_session, test_user)

    updated = update_memory(
        db_session,
        memory.id,
        {"content": "Revised.", "importance": 5},
        user_id=test_user.id,
    )

    assert updated.content == "Revised."
    assert updated.importance == 5
    assert updated.type is MemoryType.FACT


def test_updating_an_unknown_memory_raises(
    db_session: Session, test_user: User
) -> None:
    with pytest.raises(MemoryNotFoundError):
        update_memory(db_session, uuid.uuid4(), {"content": "x"}, user_id=test_user.id)


def test_a_memory_can_be_retired(db_session: Session, test_user: User) -> None:
    memory = _seed(db_session, test_user)

    assert (
        deactivate_memory(db_session, memory.id, user_id=test_user.id).active is False
    )


def test_a_memory_can_be_deleted(db_session: Session, test_user: User) -> None:
    memory = _seed(db_session, test_user)

    delete_memory(db_session, memory.id, user_id=test_user.id)

    assert list_memories(db_session, user_id=test_user.id) == []


def test_deleting_an_unknown_memory_raises(
    db_session: Session, test_user: User
) -> None:
    with pytest.raises(MemoryNotFoundError):
        delete_memory(db_session, uuid.uuid4(), user_id=test_user.id)


# --- what reaches a prompt ------------------------------------------------


def test_active_memories_exclude_retired_ones(
    db_session: Session, test_user: User
) -> None:
    """A retired memory must not keep shaping answers — that is the whole
    point of retiring it rather than deleting it."""

    kept = _seed(db_session, test_user, content="still true")
    retired = _seed(db_session, test_user, content="no longer true")
    deactivate_memory(db_session, retired.id, user_id=test_user.id)

    assert [
        memory.content for memory in active_memories(db_session, user_id=test_user.id)
    ] == ["still true"]
    assert kept.active is True


def test_active_memories_exclude_expired_ones(
    db_session: Session, test_user: User
) -> None:
    """An expiry that is not enforced is a comment."""

    _seed(db_session, test_user, content="current")
    _seed(
        db_session,
        test_user,
        content="stale",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert [
        memory.content for memory in active_memories(db_session, user_id=test_user.id)
    ] == ["current"]


def test_a_future_expiry_does_not_exclude(db_session: Session, test_user: User) -> None:
    _seed(
        db_session,
        test_user,
        content="current",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    assert len(active_memories(db_session, user_id=test_user.id)) == 1


def test_active_memories_are_ordered_by_importance(
    db_session: Session, test_user: User
) -> None:
    _seed(db_session, test_user, content="low", importance=1)
    _seed(db_session, test_user, content="high", importance=5)

    assert [
        memory.content for memory in active_memories(db_session, user_id=test_user.id)
    ] == ["high", "low"]


def test_active_memories_are_capped(db_session: Session, test_user: User) -> None:
    """The prompt has a budget, and an uncapped list is what spends it."""

    for index in range(12):
        _seed(db_session, test_user, content=f"memory {index}", importance=3)

    assert len(active_memories(db_session, limit=8, user_id=test_user.id)) == 8


def test_the_cap_keeps_the_most_important(db_session: Session, test_user: User) -> None:
    """Dropping the least important is the only defensible way to lose one."""

    _seed(db_session, test_user, content="critical", importance=5)
    for index in range(10):
        _seed(db_session, test_user, content=f"routine {index}", importance=1)

    kept = active_memories(db_session, limit=3, user_id=test_user.id)

    assert kept[0].content == "critical"
    assert len(kept) == 3


def test_the_cap_defaults_to_the_configured_maximum(
    db_session: Session, test_user: User
) -> None:
    from app.config.settings import settings

    for index in range(settings.MAX_MEMORIES_IN_CONTEXT + 5):
        _seed(db_session, test_user, content=f"memory {index}")

    assert (
        len(active_memories(db_session, user_id=test_user.id))
        == settings.MAX_MEMORIES_IN_CONTEXT
    )


# --- isolation ------------------------------------------------------------


def test_one_user_never_sees_anothers_memories(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    _seed(db_session, test_user, content="mine")
    _seed(db_session, test_user_b, content="theirs")

    assert [m.content for m in list_memories(db_session, user_id=test_user.id)] == [
        "mine"
    ]
    assert [m.content for m in list_memories(db_session, user_id=test_user_b.id)] == [
        "theirs"
    ]


def test_context_selection_is_scoped_to_one_user(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """The query chat actually runs. If isolation fails anywhere, this is the
    failure that reaches a prompt."""

    _seed(db_session, test_user, content="mine", importance=1)
    _seed(db_session, test_user_b, content="theirs", importance=5)

    assert [m.content for m in active_memories(db_session, user_id=test_user.id)] == [
        "mine"
    ]


def test_another_users_memory_cannot_be_updated(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """A 404, not a refusal: telling the caller it exists is itself a fact
    about somebody else's Digital Twin."""

    theirs = _seed(db_session, test_user_b, content="theirs")

    with pytest.raises(MemoryNotFoundError):
        update_memory(
            db_session, theirs.id, {"content": "hijacked"}, user_id=test_user.id
        )

    db_session.refresh(theirs)
    assert theirs.content == "theirs"


def test_another_users_memory_cannot_be_deleted(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    theirs = _seed(db_session, test_user_b, content="theirs")

    with pytest.raises(MemoryNotFoundError):
        delete_memory(db_session, theirs.id, user_id=test_user.id)

    assert len(list_memories(db_session, user_id=test_user_b.id)) == 1


def test_another_users_memory_cannot_be_retired(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    theirs = _seed(db_session, test_user_b, content="theirs")

    with pytest.raises(MemoryNotFoundError):
        deactivate_memory(db_session, theirs.id, user_id=test_user.id)

    db_session.refresh(theirs)
    assert theirs.active is True


def test_retiring_one_users_memory_leaves_anothers_active(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    mine = _seed(db_session, test_user, content="mine")
    _seed(db_session, test_user_b, content="theirs")

    deactivate_memory(db_session, mine.id, user_id=test_user.id)

    assert active_memories(db_session, user_id=test_user.id) == []
    assert len(active_memories(db_session, user_id=test_user_b.id)) == 1


def test_expiry_is_evaluated_per_user(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    _seed(
        db_session,
        test_user,
        content="mine, stale",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    _seed(db_session, test_user_b, content="theirs, current")

    assert active_memories(db_session, user_id=test_user.id) == []
    assert len(active_memories(db_session, user_id=test_user_b.id)) == 1
