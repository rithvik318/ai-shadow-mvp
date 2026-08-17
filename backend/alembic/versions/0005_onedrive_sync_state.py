"""Remember where each OneDrive folder's synchronisation got to.

One row per configured source. The column that matters is `delta_link`: an
opaque URL from Microsoft Graph meaning "everything up to here has been seen".
Without it every run would re-enumerate and re-download the whole corpus to
discover that nothing changed.

Keyed by `source_key` — the stable name from configuration — rather than by
the Graph drive or item id. Those are discovered on first contact and cached
here, but they are not identity: a source re-pointed at a different folder is
still the same source, and should resume rather than start again.

No foreign key to `documents`. Sync state is about a *folder*, and the link
between a document and where it came from already exists as
`documents.source_uri`, added in migration 0004.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("never_run", "running", "succeeded", "partial", "failed")


def upgrade() -> None:
    op.create_table(
        "onedrive_sync_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=512), nullable=True),
        sa.Column("drive_id", sa.String(length=512), nullable=True),
        sa.Column("item_id", sa.String(length=512), nullable=True),
        # Text rather than a bounded string: a Graph delta link is a URL
        # carrying an opaque token of unspecified length, and truncating one
        # would produce a token that looks valid and is not.
        sa.Column("delta_link", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*_STATUSES, name="sync_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("last_indexed", sa.Integer(), nullable=False),
        sa.Column("last_replaced", sa.Integer(), nullable=False),
        sa.Column("last_unchanged", sa.Integer(), nullable=False),
        sa.Column("last_deleted", sa.Integer(), nullable=False),
        sa.Column("last_unsupported", sa.Integer(), nullable=False),
        sa.Column("last_failed", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint("source_key", name="uq_onedrive_sync_state_source_key"),
    )
    op.create_index(
        "ix_onedrive_sync_state_source_key", "onedrive_sync_state", ["source_key"]
    )
    op.create_index("ix_onedrive_sync_state_status", "onedrive_sync_state", ["status"])


def downgrade() -> None:
    op.drop_index("ix_onedrive_sync_state_status", table_name="onedrive_sync_state")
    op.drop_index("ix_onedrive_sync_state_source_key", table_name="onedrive_sync_state")
    op.drop_table("onedrive_sync_state")
