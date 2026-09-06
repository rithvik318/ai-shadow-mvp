"""Widen the task lifecycle, and give a person's meetings somewhere to live.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-31

Two changes that arrive together because they serve one feature — the weekly
report — and splitting them would mean a deployment in which the report asks
for a table that is not there yet.

**`pending` becomes `todo`, and `in_progress` is added.** The stored status is
the lifecycle a person controls, and "pending" said nothing about whether
anybody had started. This is a *data* migration only. The enum columns in this
schema are plain `VARCHAR` with no CHECK constraint — `sa.Enum(...,
native_enum=False)` has defaulted to `create_constraint=False` since SQLAlchemy
1.4, so the constraints the surrounding comments describe were never emitted,
here or on any other table. Verified against a real PostgreSQL 16 database:
`pg_constraint` holds no check rows for `task`. So widening the vocabulary
needs no DDL, and emitting `ALTER COLUMN` statements that change nothing would
be code implying a guarantee the database does not give. The gap is recorded
in docs/KNOWN_ISSUES.md rather than closed here, because adding constraints to
every enum column in the schema is its own change.

**`calendar_event`.** One row per person per meeting — attendance is a fact
about a person, not about a meeting, so two invitees are two rows. `status`
holds only what was recorded; a finished meeting nobody has answered for stays
`scheduled` here and reads as `unknown`, computed on read. Nothing in this
migration or the model can conclude that somebody attended anything.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None

_EVENT_STATUSES = ("scheduled", "attended", "missed", "cancelled", "unknown")
_EVIDENCE = ("user", "derived", "none")


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE task SET status = 'todo' WHERE status = 'pending'")
    )

    op.create_table(
        "calendar_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            # Cascade: an event is meaningless without the person whose week it
            # is, and deleting a user must leave no orphan rows.
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*_EVENT_STATUSES, name="event_status", native_enum=False),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "attendance_evidence",
            sa.Enum(*_EVIDENCE, name="attendance_evidence", native_enum=False),
            nullable=False,
            server_default="none",
        ),
        sa.Column("attendance_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attendance_note", sa.Text(), nullable=True),
        sa.Column("organiser_name", sa.String(length=255), nullable=True),
        sa.Column("organiser_address", sa.String(length=320), nullable=True),
        sa.Column(
            "source", sa.String(length=64), nullable=False, server_default="manual"
        ),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_event_id", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Nullable columns, so several manually created events may share a NULL
        # provider id without colliding.
        sa.UniqueConstraint(
            "user_id", "provider_event_id", name="uq_event_provider_id"
        ),
    )

    op.create_index("ix_calendar_event_user_id", "calendar_event", ["user_id"])
    op.create_index(
        "ix_calendar_event_user_starts", "calendar_event", ["user_id", "starts_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_calendar_event_user_starts", table_name="calendar_event")
    op.drop_index("ix_calendar_event_user_id", table_name="calendar_event")
    op.drop_table("calendar_event")

    # `in_progress` has no pre-0011 equivalent. It becomes `pending`, which is
    # the honest reversal: the old vocabulary could not express "started", so
    # going back necessarily loses that distinction rather than inventing a
    # place to keep it.
    op.get_bind().execute(
        sa.text(
            "UPDATE task SET status = 'pending' WHERE status IN ('todo', 'in_progress')"
        )
    )
