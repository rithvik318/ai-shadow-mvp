"""What somebody still has to do, and where it came from.

One table. A task is owned by exactly one person — `user_id` is a foreign key
into `users`, the same private ownership the Digital Twin uses, not the shared
string the knowledge base uses. One person's list is never another's.

Three decisions are worth stating because each is easy to get wrong later:

**`status` holds only what a person controls.** `todo`, `in_progress`,
`completed`, `blocked`, `cancelled` — and deliberately not `overdue` or
`escalation_required`. Both of those are facts about the clock and the rules,
and a stored copy of either is wrong from the moment it is written until
something rewrites it. They are computed on read, in
`services/features/tasks/urgency.py` and
`services/features/tasks/escalation.py`.

**`completed_at` is the evidence, not an inference.** Nothing in this system
sets it because a due date passed, or because an email was sent, or because a
thread went quiet. It is set when a person completes the task or when explicit
evidence says so.

**A task from an email carries a stable source key.** Triage runs repeatedly
over the same inbox, and without an identity for "the task this message already
produced" every run would add a duplicate. `source_key` is that identity, and
it is unique per user.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UtcDateTime
from app.services.features.tasks.urgency import TaskPriority, TaskStatus

# varchar + CHECK rather than a native PG enum, matching the rest of the
# schema: adding a value to a native enum is a migration with a lock, and a
# CHECK constraint says the same thing without one.
TaskStatusType = SAEnum(
    TaskStatus,
    name="task_status",
    native_enum=False,
    values_callable=lambda enum: [member.value for member in enum],
)

TaskPriorityType = SAEnum(
    TaskPriority,
    name="task_priority",
    native_enum=False,
    values_callable=lambda enum: [member.value for member in enum],
)


class Task(Base):
    """One thing one person still has to do."""

    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[TaskStatus] = mapped_column(
        TaskStatusType, nullable=False, default=TaskStatus.TODO
    )
    priority: Mapped[TaskPriority] = mapped_column(
        TaskPriorityType, nullable=False, default=TaskPriority.NORMAL
    )

    # Only ever a date somebody or something *stated*. A task with no stated
    # deadline has none here — an invented one would drive an amber badge and
    # an escalation off a date nobody ever gave.
    due_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # --- provenance ------------------------------------------------------
    # Where this came from, in words a person can read: "manual",
    # "email_triage", "email_follow_up". Free text rather than an enum because
    # a new source should not need a migration to be describable.
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    # The deduplication identity. Null for a manually created task, because two
    # manual tasks with the same title are two tasks somebody meant to make.
    source_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Deliberately SET NULL, not CASCADE: re-triaging a message may replace the
    # assessment row, and a task somebody is working on must not vanish
    # because the judgement that suggested it was superseded.
    source_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("email_assessment.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- the person on the other end -------------------------------------
    # Split for the same reason the assessment's sender is split: a name is
    # for display, an address is what a message would actually go to.
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_address: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # --- escalation ------------------------------------------------------
    # Set when the source material *said* escalation was needed. Distinct from
    # the escalation the weekly report derives from age and priority: this one
    # came from a human, and is not recomputed away.
    escalation_requested: Mapped[bool] = mapped_column(
        # `false()` — the SQL literal — not `func.false()`, which renders as a
        # function *call*: `DEFAULT false()`. PostgreSQL has no such function
        # and rejects the CREATE TABLE outright. This is the same construct
        # `users.is_admin` already uses, and it renders correctly on every
        # dialect the project targets.
        nullable=False,
        default=False,
        server_default=false(),
    )
    escalation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # The whole duplicate-prevention guarantee, in one line. Scoped to the
        # user so two people can each hold a task derived from the same
        # broadcast message.
        UniqueConstraint("user_id", "source_key", name="uq_task_source_key"),
        # The weekly report's access pattern: one person's open work, by date.
        Index("ix_task_user_status_due", "user_id", "status", "due_at"),
    )

    @property
    def contact_display(self) -> str | None:
        """How to show the contact — never what to send to."""

        if self.contact_name and self.contact_address:
            return f"{self.contact_name} <{self.contact_address}>"

        return self.contact_address or self.contact_name
