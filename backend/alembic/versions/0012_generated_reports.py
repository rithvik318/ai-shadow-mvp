"""Give generated reports somewhere to live, so history stops being a rerun.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-02

Until now every report was recomputed on request, which is fine for "what does
my week look like" and wrong for everything else: a weekly work report rerun in
October describes October's tasks, and an email digest cannot be rerun at all
once the provider has aged the messages out. `generated_report` stores the
rendered report so a period keeps saying what it said.

The unique constraint on `(user_id, report_type, period_start)` is the whole
duplicate-prevention guarantee, and it is a database constraint rather than a
check in the service because the scheduler and a person pressing Generate can
race. Scoped to the user for the same reason every other table here is: two
people's reports of the same week are two rows, and one is never visible to the
other.

`content` is JSONB on PostgreSQL and JSON elsewhere, matching `email_*`. It is
denormalised on purpose — a snapshot that referenced `task.id` would change
when somebody renamed a task, which is the one thing a snapshot must not do.

No CHECK constraints on the two enum columns, consistent with every other enum
column in this schema: `sa.Enum(..., native_enum=False)` has defaulted to
`create_constraint=False` since SQLAlchemy 1.4, so none of them has ever had
one. Recorded in docs/KNOWN_ISSUES.md rather than closed here for one table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None

_REPORT_TYPES = ("weekly_work", "weekly_email_digest", "monthly_email_digest")
_REPORT_STATUSES = ("complete", "unavailable")


def upgrade() -> None:
    op.create_table(
        "generated_report",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "report_type",
            sa.Enum(*_REPORT_TYPES, name="report_type", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(*_REPORT_STATUSES, name="report_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "is_provisional",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "content",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "report_type",
            "period_start",
            name="uq_generated_report_period",
        ),
    )

    op.create_index(
        "ix_generated_report_user_id", "generated_report", ["user_id"], unique=False
    )
    op.create_index(
        "ix_generated_report_user_type_period",
        "generated_report",
        ["user_id", "report_type", "period_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generated_report_user_type_period", table_name="generated_report")
    op.drop_index("ix_generated_report_user_id", table_name="generated_report")
    op.drop_table("generated_report")
