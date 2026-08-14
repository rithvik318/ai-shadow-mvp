"""Create digital_twin_profile and digital_twin_memory.

The Digital Twin layer: who the Shadow answers for, and what it durably knows
about them. Nothing here touches documents, chunks or the vector column.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Lists of short strings. JSONB on Postgres, matching the model's variant.
STRING_LIST = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "digital_twin_profile",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=False),
        sa.Column("organization", sa.String(length=255), nullable=False),
        sa.Column("communication_style", sa.Text(), nullable=True),
        sa.Column("responsibilities", STRING_LIST, nullable=False),
        sa.Column("expertise", STRING_LIST, nullable=False),
        sa.Column("priorities", STRING_LIST, nullable=False),
        sa.Column("decision_preferences", STRING_LIST, nullable=False),
        sa.Column("current_focus", STRING_LIST, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # One profile per owner, enforced by the schema: "the active profile"
        # has to be a fact, not a convention.
        sa.UniqueConstraint("user_id", name="uq_digital_twin_profile_user"),
    )
    op.create_index(
        "ix_digital_twin_profile_user_id", "digital_twin_profile", ["user_id"]
    )

    op.create_table(
        "digital_twin_memory",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "fact",
                "preference",
                "decision",
                "commitment",
                "context",
                name="memory_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_digital_twin_memory_user_id", "digital_twin_memory", ["user_id"]
    )
    op.create_index("ix_digital_twin_memory_type", "digital_twin_memory", ["type"])
    # Covers the only hot query: the active memories of one owner, most
    # important first.
    op.create_index(
        "ix_digital_twin_memory_user_active",
        "digital_twin_memory",
        ["user_id", "active", "importance"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_digital_twin_memory_user_active", table_name="digital_twin_memory"
    )
    op.drop_index("ix_digital_twin_memory_type", table_name="digital_twin_memory")
    op.drop_index("ix_digital_twin_memory_user_id", table_name="digital_twin_memory")
    op.drop_table("digital_twin_memory")

    op.drop_index("ix_digital_twin_profile_user_id", table_name="digital_twin_profile")
    op.drop_table("digital_twin_profile")
