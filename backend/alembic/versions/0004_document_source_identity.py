"""Give documents an identity that is not their filename.

Ingestion could previously not tell whether it had seen a file before. The only
handle on a document was its name, and two unrelated files are routinely both
called `proposal.pdf` — so re-uploading a corpus produced duplicates, and an
edited file produced a second document rather than a new version of the first.

Three columns fix that, and are the contract the later OneDrive synchronisation
will use:

`content_hash` is the sha256 of the uploaded bytes and answers "are these the
same file?". It is **nullable on purpose**. Documents ingested before this
migration have no stored bytes to hash, and there is no honest value to invent
for them — the file they came from may no longer exist, and a synthetic hash
would be a claim about content nobody verified. NULL means "identity unknown",
and because NULL never compares equal to a digest, those rows simply never
match a new upload. They are never wrongly deduplicated; at worst a re-upload
of one creates a second row, which is the behaviour that already existed.

`source_uri` is a stable identifier for where a file came from — a OneDrive
item id, later. It is what distinguishes "this document changed" from "this is
a different document", which a content hash cannot do on its own, since the
hash is precisely what changed. It is unique per user where present, enforced
by a partial index; NULL means a hand upload with no external source, and any
number of those may coexist.

`source_version` carries whatever version marker the source offers — an etag or
a modification time — so a sync can skip unchanged files without downloading
them to hash.

The `document_status` check constraint also gains `unsupported`, a state
distinct from `failed`: a failed document may succeed on retry, an unsupported
one cannot until a parser for its format exists.

No data is rewritten. Every column is nullable and every existing row keeps the
values it has.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Named by `sa.Enum(..., name="document_status", native_enum=False)` in
# migration 0001, which renders the constraint under exactly that name.
_STATUS_CONSTRAINT = "document_status"

_STATUSES_BEFORE = ("pending", "processing", "indexed", "failed")
_STATUSES_AFTER = ("pending", "processing", "indexed", "failed", "unsupported")


def _status_check(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({allowed})"


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("content_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_uri", sa.String(length=1024), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_version", sa.String(length=255), nullable=True)
    )

    # The lookup performed on every upload that carries no source.
    op.create_index(
        "ix_documents_user_id_content_hash", "documents", ["user_id", "content_hash"]
    )

    # One document per source, enforced by the database rather than by whichever
    # service happens to be writing. Partial: NULL source_uri means "no source",
    # and manual uploads all share that.
    op.create_index(
        "uq_documents_user_id_source_uri",
        "documents",
        ["user_id", "source_uri"],
        unique=True,
        postgresql_where=sa.text("source_uri IS NOT NULL"),
        sqlite_where=sa.text("source_uri IS NOT NULL"),
    )

    # `IF EXISTS` because the constraint is generated rather than written by
    # hand, and a database restored from a dump taken by a different SQLAlchemy
    # version may not carry it under this name. Losing the constraint is worse
    # than not having had it, so it is recreated unconditionally below.
    op.execute(f"ALTER TABLE documents DROP CONSTRAINT IF EXISTS {_STATUS_CONSTRAINT}")
    op.create_check_constraint(
        _STATUS_CONSTRAINT, "documents", _status_check(_STATUSES_AFTER)
    )


def downgrade() -> None:
    # Any document recorded as unsupported has no equivalent in the earlier
    # schema. `failed` is the closest true statement: it was not indexed, and
    # the reason is still in error_message.
    op.execute("UPDATE documents SET status = 'failed' WHERE status = 'unsupported'")

    op.execute(f"ALTER TABLE documents DROP CONSTRAINT IF EXISTS {_STATUS_CONSTRAINT}")
    op.create_check_constraint(
        _STATUS_CONSTRAINT, "documents", _status_check(_STATUSES_BEFORE)
    )

    op.drop_index("uq_documents_user_id_source_uri", table_name="documents")
    op.drop_index("ix_documents_user_id_content_hash", table_name="documents")

    op.drop_column("documents", "source_version")
    op.drop_column("documents", "source_uri")
    op.drop_column("documents", "content_hash")
