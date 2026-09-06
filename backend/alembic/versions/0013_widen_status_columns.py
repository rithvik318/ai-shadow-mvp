"""Make two status columns wide enough for the words they already hold.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-02

`sa.Enum(..., native_enum=False)` renders as `VARCHAR(n)` where *n* is the
length of the longest member **at the time the migration was written**. Add a
longer member later and the column silently stays too narrow. SQLite ignores
`VARCHAR` lengths entirely, so the whole test suite passes; PostgreSQL raises
`value too long for type character varying(n)` the first time a person uses the
feature. That is exactly what happened twice here.

**`task.status` was `VARCHAR(9)`.** Migration 0011 widened the vocabulary from
`pending` to `todo` plus `in_progress` and correctly observed that no CHECK
constraint existed to update — but the *column width* was overlooked.
`in_progress` is eleven characters, so pressing Start on a task returned a 500
on any PostgreSQL deployment while every test stayed green. Verified against a
real PostgreSQL 16 database: the `UPDATE` fails with
`value too long for type character varying(9)`.

**`documents.status` was `VARCHAR(10)`.** Same class of bug, in the ingestion
path and never noticed because it needs an unsupported file to reach it:
`unsupported` is eleven characters and could not be written, so a document
rejected for its type failed while being recorded as rejected.

**`task.status` also carried a server default of `'pending'`** — a value the
application removed in 0011 and can no longer read back. It is dropped rather
than corrected: the model declares a Python-side `default=TaskStatus.TODO` and
no server default, and a stored default the model does not know about is
precisely what let `'pending'` outlive the vocabulary. Every insert this
application makes supplies the status; a raw insert that omits it now fails
loudly on NOT NULL instead of quietly writing a status nobody chose.

Both changes are widenings, so no data is at risk and no row needs rewriting.
There is deliberately no downgrade narrowing `task.status` back to nine: rows
holding `in_progress` would be truncated, and a downgrade that destroys data is
worse than one that leaves a column roomier than it was.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 11 = len("in_progress"), the longest member of TaskStatus.
    op.alter_column(
        "task",
        "status",
        existing_type=sa.String(length=9),
        type_=sa.String(length=11),
        existing_nullable=False,
        server_default=None,
    )

    # 11 = len("unsupported"), the longest member of DocumentStatus.
    op.alter_column(
        "documents",
        "status",
        existing_type=sa.String(length=10),
        type_=sa.String(length=11),
        existing_nullable=False,
    )


def downgrade() -> None:
    # `documents.status` narrows safely: nothing shorter than eleven characters
    # is lost, and a row holding `unsupported` could not have been written by
    # the schema this reverts to.
    op.alter_column(
        "documents",
        "status",
        existing_type=sa.String(length=11),
        type_=sa.String(length=10),
        existing_nullable=False,
    )

    # `task.status` is deliberately left at eleven. Narrowing it would truncate
    # every `in_progress` row, and the old `'pending'` default names a status
    # this application cannot read. Restoring either would break the database
    # rather than restore it.
