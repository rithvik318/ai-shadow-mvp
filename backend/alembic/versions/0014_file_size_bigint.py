"""Widen `documents.file_size_bytes` to BIGINT.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-12

The column stores the size a file *reported*, not the size of something this
application accepted, and those two are bounded by different things.

An accepted upload cannot exceed `MAX_UPLOAD_SIZE_BYTES` (10 MiB by default),
so for years every value here fit comfortably in `INTEGER`. But a synchronised
file that is *rejected* for being too large is still recorded — deliberately,
because a knowledge base that cannot say what it is missing is worse than one
that can — and the size written in that case is whatever OneDrive said, bounded
only by the drive.

The first real sync of SunRadia's Knowledge Base met
`CFTC Debrief _ Microsoft Teams 2024-09-05 08-58-41.mp4` at 6,788,146,857
bytes. That is three times `INTEGER`'s ceiling of 2,147,483,647, so
`record_unsupported_source` raised `psycopg2.errors.NumericValueOutOfRange`
and — because the failure came from the database rather than from a file —
aborted the surrounding transaction and ended the run.

**Why no test caught it.** SQLite's `INTEGER` is a variable-width signed value
up to 64 bits, so it stores 6.8 billion without complaint. The whole suite
passed on SQLite while PostgreSQL could not write the row. That is precisely
the shape of the `VARCHAR`-width bug migration 0013 fixed, and it is why
`tests/database/test_migration_fidelity.py` now also checks integer widths
against the models.

**The value is not clamped, scaled or truncated.** Recording 2,147,483,647 for
a 6.8 GB file would be a lie in a column whose entire purpose is to report the
true size of something nobody could ingest.

Widening `INTEGER` to `BIGINT` preserves every existing row: PostgreSQL rewrites
the column in place and every value that fit in four bytes fits in eight.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column(
        "documents",
        "file_size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Narrow back to INTEGER.

    This will fail loudly if any row holds a value above 2,147,483,647 — and
    failing is correct. The alternative is a downgrade that silently discards
    the size of every file the knowledge base recorded as too large, which is
    a downgrade that destroys data rather than reversing a change.
    """

    op.alter_column(
        "documents",
        "file_size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
