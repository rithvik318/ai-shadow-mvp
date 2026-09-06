"""Tasks: what somebody still has to do, and where it came from.

Owned privately, like the Digital Twin — `user_id` is a foreign key into
`users` and cascades on delete, because a task is one person's work and means
nothing without them.

Two constraints carry design decisions rather than housekeeping:

`uq_task_source_key` is the whole duplicate-prevention guarantee. Triage runs
repeatedly over the same inbox; without a unique identity for "the task this
message already produced", every run would add another copy of it.

`source_assessment_id` is SET NULL rather than CASCADE. Re-assessing a message
can replace its assessment row, and a task somebody is part-way through must
not disappear because the judgement that suggested it was superseded.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None

# Mirrors app/services/features/tasks/urgency.py. `overdue` is absent on
# purpose: it is derived from `due_at` and the clock, never stored.
_STATUSES = ("pending", "completed", "blocked", "cancelled")
_PRIORITIES = ("low", "normal", "high", "urgent")


def upgrade() -> None:
    op.create_table(
        "task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*_STATUSES, name="task_status", native_enum=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "priority",
            sa.Enum(*_PRIORITIES, name="task_priority", native_enum=False),
            nullable=False,
            server_default="normal",
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source", sa.String(length=64), nullable=False, server_default="manual"
        ),
        sa.Column("source_key", sa.String(length=512), nullable=True),
        sa.Column("source_provider", sa.String(length=64), nullable=True),
        sa.Column("source_message_id", sa.String(length=512), nullable=True),
        sa.Column("source_thread_id", sa.String(length=512), nullable=True),
        sa.Column("source_assessment_id", sa.Uuid(), nullable=True),
        sa.Column("contact_name", sa.String(length=255), nullable=True),
        sa.Column("contact_address", sa.String(length=320), nullable=True),
        sa.Column(
            "escalation_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("escalation_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_task_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_assessment_id"],
            ["email_assessment.id"],
            name="fk_task_assessment",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("user_id", "source_key", name="uq_task_source_key"),
    )

    op.create_index("ix_task_user_id", "task", ["user_id"])
    op.create_index("ix_task_user_status_due", "task", ["user_id", "status", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_task_user_status_due", table_name="task")
    op.drop_index("ix_task_user_id", table_name="task")
    op.drop_table("task")
