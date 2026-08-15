"""Durable memories: what the Shadow knows that no document says.

Everything here is written explicitly, through the API. Nothing is extracted
from chat or from ingested documents — a store that fills itself becomes a
transcript, and a transcript is not memory: it grows without bound, it is
mostly noise, and nobody can say why any particular line is in the prompt.

Retrieval is deliberately not semantic. The store is small and structured, so
"the most important active memories" is a sort, not a search. Embedding them
would add a provider call, an index and a failure mode to a query that already
returns the right answer.

Every function takes a required `user_id`, and every query in this module
filters on it — including the lookups behind update and delete, which is what
makes another user's memory indistinguishable from one that does not exist.
There is no default owner: a default is a global store that a forgotten
argument reaches, and one missing keyword should not be the distance between
two people's memories.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import MemoryNotFoundError
from app.models.digital_twin import DigitalTwinMemory, MemoryType

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = ("type", "content", "importance", "source", "active", "expires_at")


def create_memory(
    db: Session,
    *,
    memory_type: MemoryType,
    content: str,
    importance: int = 3,
    source: str = "user",
    expires_at: datetime | None = None,
    user_id: uuid.UUID,
) -> DigitalTwinMemory:
    """Store one memory."""

    memory = DigitalTwinMemory(
        user_id=user_id,
        type=memory_type,
        content=content.strip(),
        importance=importance,
        source=source,
        active=True,
        expires_at=expires_at,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)

    logger.info(
        "digital_twin_memory_created",
        extra={
            "user_id": str(user_id),
            "memory_id": str(memory.id),
            "type": memory.type.value,
            "importance": memory.importance,
        },
    )

    return memory


def list_memories(
    db: Session,
    *,
    memory_type: MemoryType | None = None,
    active: bool | None = None,
    user_id: uuid.UUID,
) -> list[DigitalTwinMemory]:
    """Every memory matching the filters, most important first.

    Unfiltered by default — this is the management view, so a retired memory is
    still something the caller may want to see. `active_memories` is what chat
    uses, and that one filters.
    """

    predicates = [DigitalTwinMemory.user_id == user_id]

    if memory_type is not None:
        predicates.append(DigitalTwinMemory.type == memory_type)

    if active is not None:
        predicates.append(DigitalTwinMemory.active.is_(active))

    return list(
        db.execute(
            select(DigitalTwinMemory)
            .where(*predicates)
            .order_by(
                DigitalTwinMemory.importance.desc(),
                DigitalTwinMemory.created_at.desc(),
                # Ties broken on id so two memories written in the same
                # transaction cannot swap places between calls.
                DigitalTwinMemory.id,
            )
        )
        .scalars()
        .all()
    )


def get_memory(
    db: Session, memory_id: uuid.UUID, *, user_id: uuid.UUID
) -> DigitalTwinMemory:
    """Return this user's memory, or raise.

    The `user_id` predicate is what makes another user's memory a 404 rather
    than a 403: refusing tells the caller the memory exists, which is a fact
    about somebody else's Digital Twin.
    """

    memory = db.execute(
        select(DigitalTwinMemory).where(
            DigitalTwinMemory.id == memory_id,
            DigitalTwinMemory.user_id == user_id,
        )
    ).scalar_one_or_none()

    if memory is None:
        raise MemoryNotFoundError(f"Memory not found: {memory_id}")

    return memory


def update_memory(
    db: Session,
    memory_id: uuid.UUID,
    values: dict[str, object],
    *,
    user_id: uuid.UUID,
) -> DigitalTwinMemory:
    """Change the fields the caller supplied, and only those."""

    memory = get_memory(db, memory_id, user_id=user_id)

    for field, value in values.items():
        if field in EDITABLE_FIELDS:
            setattr(memory, field, value)

    db.commit()
    db.refresh(memory)

    return memory


def deactivate_memory(
    db: Session, memory_id: uuid.UUID, *, user_id: uuid.UUID
) -> DigitalTwinMemory:
    """Retire a memory without destroying it.

    A decision that no longer applies is still a thing that was decided, and
    the record of it is occasionally the answer to "why did we do that?".
    """

    return update_memory(db, memory_id, {"active": False}, user_id=user_id)


def delete_memory(db: Session, memory_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Remove a memory for good. Used when it should never have been stored."""

    memory = get_memory(db, memory_id, user_id=user_id)
    db.delete(memory)
    db.commit()

    logger.info(
        "digital_twin_memory_deleted",
        extra={"user_id": str(user_id), "memory_id": str(memory_id)},
    )


def active_memories(
    db: Session,
    *,
    limit: int | None = None,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> list[DigitalTwinMemory]:
    """The memories that should shape the next answer.

    Active, unexpired, most important first, and capped — because the prompt
    has a budget and an uncapped list is the thing that eventually spends it.
    """

    moment = now or datetime.now(UTC)
    cap = limit if limit is not None else settings.MAX_MEMORIES_IN_CONTEXT

    memories = db.execute(
        select(DigitalTwinMemory)
        .where(
            DigitalTwinMemory.user_id == user_id,
            DigitalTwinMemory.active.is_(True),
            or_(
                DigitalTwinMemory.expires_at.is_(None),
                DigitalTwinMemory.expires_at > moment,
            ),
        )
        .order_by(
            DigitalTwinMemory.importance.desc(),
            DigitalTwinMemory.created_at.desc(),
            DigitalTwinMemory.id,
        )
        .limit(max(cap, 0))
    )

    return list(memories.scalars().all())
