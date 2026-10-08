"""Store the comprehensive activity report instead of recomputing it.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-15

`GET /reports/activity` produced its report on every request: a mailbox read,
classification, threading, a dozen model calls and a composition, for a period
that had closed weeks earlier and could not possibly have changed. Opening the
same historical week twice cost the same minutes twice.

This table is where that report is written down. Its identity is
`(user_id, period_start, period_end, report_version)` — the exact window rather
than a period key, because the endpoint takes two dates and "1–13 September" is
not any calendar period, and the version because a report is the output of a
prompt and a composition that will change. Raising the version retires every
older row by making it not match; nothing is deleted and no old report is ever
served as though this month's machinery had produced it.

Separate from `generated_report` on purpose, argued in the model's docstring:
that table's rule is that a stored report is immutable and a row is either the
report or an explanation of its absence. This one needs a `generating` state,
which is a claim on work in progress rather than a report, and it is the
uniqueness of `(user, window, version)` that makes that claim safe under two
simultaneous requests.

`content` holds the rendered document and its counts. It holds no message
bodies, subjects or addresses: the correspondence a report was built from stays
in the mailbox, and `evidence_fingerprint` — message ids and counts, hashed —
is what identifies it here.

No CHECK constraint on the status column, consistent with every other enum
column in this schema; `sa.Enum(..., native_enum=False)` has defaulted to
`create_constraint=False` since SQLAlchemy 1.4. The column is `VARCHAR(32)`
rather than sized to today's longest member, which is the lesson migrations
0013 and 0015 exist to record.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None

_STATUSES = ("generating", "complete", "failed")


def upgrade() -> None:
    op.create_table(
        "activity_report",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_version", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *_STATUSES,
                name="activity_report_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_provisional",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=True),
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
            "period_start",
            "period_end",
            "report_version",
            name="uq_activity_report_window",
        ),
    )

    op.create_index(
        "ix_activity_report_user_id", "activity_report", ["user_id"], unique=False
    )
    op.create_index(
        "ix_activity_report_user_window",
        "activity_report",
        ["user_id", "period_start", "period_end"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_activity_report_user_window", table_name="activity_report")
    op.drop_index("ix_activity_report_user_id", table_name="activity_report")
    op.drop_table("activity_report")
