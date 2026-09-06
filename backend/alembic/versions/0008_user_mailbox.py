"""Give each person their own mailbox instead of one address in the environment.

`EMAIL_MAILBOX_ADDRESS` made the mailbox a property of the deployment. Two
people using the same server read the same inbox and sent from the same
address, and changing whose mailbox it was meant editing `.env` and
restarting — which is not multi-user, it is single-user with extra steps.

A row here owns the connection for exactly one person. `user_id` is a unique
foreign key rather than a plain column: one mailbox per person is the rule, and
a unique constraint is the only version of that rule the database can enforce.

No credentials are stored. Graph application permissions carry no user, so the
only thing that varies per person is *which* mailbox to act on; the client
credentials stay in configuration where they already were.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "user_mailbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_user_mailbox"),
        # Deleting a person takes their mailbox configuration with them. It is
        # theirs, it names their inbox, and leaving it behind would orphan a
        # row that points at a person who no longer exists.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_mailbox_user",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="uq_user_mailbox_user"),
    )


def downgrade() -> None:
    op.drop_table("user_mailbox")
