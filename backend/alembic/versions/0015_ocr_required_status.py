"""Give "no text layer" a status of its own, and a column wide enough for it.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-13

Thirty files in the SunRadia knowledge base are scans: the format is
supported, the download succeeded, the parser ran to completion, and there is
no text in the document. Until now every one of them was counted as an
application failure, which is wrong in both directions — it inflates the
failure count of a healthy run, and it hides a backlog that has one specific
remedy nobody can apply by retrying.

`documents.status` gains `ocr_required`, and the column is widened from
`VARCHAR(11)` to `VARCHAR(12)` to hold it. That widening is the whole reason
this migration is not just an enum edit: `sa.Enum(..., native_enum=False)`
renders as `VARCHAR(n)` sized to the longest member *when the migration was
written*, SQLite ignores the length entirely, and PostgreSQL raises
`value too long for type character varying(11)` the first time the new value
is written. Migration 0013 exists because that happened twice already; this is
the same trap, avoided rather than repeated.

`onedrive_sync_state.last_ocr_required` is the matching counter, so a run's
report can say how many files need OCR without recomputing it from the
documents table.

Both changes are additive. No existing row holds either value, no data is at
risk, and the downgrade is exact: the column narrows back to eleven only after
any `ocr_required` row has been returned to `failed`, which is where such a row
would have been under the schema this reverts to.

There are **two** things to change for a new enum member, and only widening the
column is the obvious one. `sa.Enum(..., native_enum=False)` also renders a
`CHECK (status IN (...))` constraint, named `document_status` since migration
0001 and last rewritten by 0004. Add a member to the Python enum without
rewriting that constraint and PostgreSQL answers
`new row for relation "documents" violates check constraint "document_status"`
— which the test suite cannot see, because it builds its schema from the models
rather than from the migrations, so the constraint it creates already lists the
new value. Found by running this migration against a real PostgreSQL 16 and
trying to insert the row.

A regression test now covers the general case: every enum column's stored CHECK
constraint has to accept every member its model declares.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None

# Named by `sa.Enum(..., name="document_status", native_enum=False)` in
# migration 0001 and rewritten by 0004, under exactly that name.
_STATUS_CONSTRAINT = "document_status"

_STATUSES_BEFORE = ("pending", "processing", "indexed", "failed", "unsupported")
_STATUSES_AFTER = (*_STATUSES_BEFORE, "ocr_required")


def _status_check(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({allowed})"


def upgrade() -> None:
    # 12 = len("ocr_required"), the longest member of DocumentStatus.
    op.alter_column(
        "documents",
        "status",
        existing_type=sa.String(length=11),
        type_=sa.String(length=12),
        existing_nullable=False,
    )

    # `IF EXISTS` for the same reason 0004 used it: the constraint is generated
    # rather than written by hand, and a database restored from a dump taken by
    # a different SQLAlchemy version may not carry it under this name. It is
    # recreated unconditionally below either way.
    op.execute(f"ALTER TABLE documents DROP CONSTRAINT IF EXISTS {_STATUS_CONSTRAINT}")
    op.create_check_constraint(
        _STATUS_CONSTRAINT, "documents", _status_check(_STATUSES_AFTER)
    )

    op.add_column(
        "onedrive_sync_state",
        sa.Column(
            "last_ocr_required",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("onedrive_sync_state", "last_ocr_required")

    # Rows first, or the narrowing truncates them into a status nothing reads.
    # `failed` is where these documents sat before this migration, so putting
    # them back there is a restoration rather than a guess.
    op.execute("UPDATE documents SET status = 'failed' WHERE status = 'ocr_required'")

    op.execute(f"ALTER TABLE documents DROP CONSTRAINT IF EXISTS {_STATUS_CONSTRAINT}")
    op.create_check_constraint(
        _STATUS_CONSTRAINT, "documents", _status_check(_STATUSES_BEFORE)
    )

    op.alter_column(
        "documents",
        "status",
        existing_type=sa.String(length=12),
        type_=sa.String(length=11),
        existing_nullable=False,
    )
