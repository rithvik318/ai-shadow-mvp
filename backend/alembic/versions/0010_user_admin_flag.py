"""Make "administrator" a fact the server can check, not a job title.

`users.role` is free text — "CEO", "CRM Manager" — and it describes the persona
the Digital Twin writes as. Nothing branched on it, and nothing should: a
string somebody types is not an authorisation decision, and treating it as one
would mean the person who names themselves "Admin" becomes one.

`is_admin` is a separate boolean for exactly that reason. It is the minimum
needed for the one operation that is not self-service — deleting another user
— and deliberately not a role table, a permission matrix or a grant system
nobody has asked for.

Defaults to false. An existing deployment gains no administrators by upgrading;
the first one is granted deliberately.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
