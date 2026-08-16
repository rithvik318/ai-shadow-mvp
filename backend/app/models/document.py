import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.settings import settings
from app.database.base import Base


class DocumentStatus(StrEnum):
    """Lifecycle of an uploaded document.

    `unsupported` is distinct from `failed`: a failed document is one this
    system tried and could not finish, and retrying it may work. An
    unsupported one will never succeed until a parser for its format exists,
    so a sync source that records it can stop offering it. Only ingestion
    carrying a `source_uri` persists this state — a manual upload of an
    unsupported file is still rejected outright, leaving no row.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class IngestionResult(StrEnum):
    """What an ingestion call did, as opposed to the state it left behind.

    Deliberately separate from `DocumentStatus`. A skipped re-upload and a
    fresh ingest both leave a document `indexed`; only this distinguishes
    them, and a caller synchronising a corpus needs that difference to report
    anything meaningful about a run. Kept here beside the status it is so
    easily confused with, and because schemas may read the models layer but
    not the service layer.
    """

    INDEXED = "indexed"
    UNCHANGED = "unchanged"
    REPLACED = "replaced"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


SUCCESSFUL_INGESTION_RESULTS = frozenset(
    {IngestionResult.INDEXED, IngestionResult.UNCHANGED, IngestionResult.REPLACED}
)


# SQLAlchemy persists a PEP-435 enum by member *name* unless told otherwise,
# which would write "PENDING" while the migration's CHECK constraint expects
# "pending". `values_callable` makes the stored form the member value.
DocumentStatusType = SAEnum(
    DocumentStatus,
    name="document_status",
    native_enum=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


# pgvector is the production type. SQLite has no vector type, so the test
# suite stores the same column as JSON — the column exists in both dialects,
# which is what lets the embedding feature populate it without a migration.
#
# `none_as_null=True` is load-bearing. SQLAlchemy's JSON type defaults to
# persisting a Python None as the JSON encoding of `null` rather than as SQL
# NULL, and it sets `should_evaluate_none` so an unset attribute is bound too.
# Under that default, `embedding IS NULL` matches nothing on SQLite while
# matching correctly against pgvector — so every query that asks "which chunks
# still need a vector?" silently returns nothing, and the whole embedding
# pipeline no-ops in tests only. The variant has to agree with production
# about what "no vector" means, or it is not a stand-in for it.
EmbeddingType = Vector(settings.EMBEDDING_DIMENSIONS).with_variant(
    JSON(none_as_null=True), "sqlite"
)


class Document(Base):
    """An uploaded source document and the state of its ingestion."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- source identity -------------------------------------------------
    #
    # What makes two uploads "the same document". Filename alone is not an
    # identity: two unrelated files are routinely both called `proposal.pdf`.
    #
    # `content_hash` is the sha256 of the uploaded bytes. It is nullable, and
    # NULL means "identity unknown" — documents ingested before this column
    # existed carry NULL, and a NULL never compares equal to a digest, so a
    # legacy row is never mistaken for a duplicate of a new upload.
    #
    # `source_uri` is a stable identifier from wherever the file came from —
    # a OneDrive item id, later. It is what lets a *changed* file be
    # recognised as a new version of a document rather than a new document,
    # which content_hash alone cannot do: the hash is what changed.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(255), nullable=True)

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[DocumentStatus] = mapped_column(
        DocumentStatusType,
        nullable=False,
        default=DocumentStatus.PENDING,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_documents_user_id_created_at", "user_id", "created_at"),
        # The duplicate lookup on every upload that carries no source.
        Index("ix_documents_user_id_content_hash", "user_id", "content_hash"),
        # One document per source, enforced by the database rather than by the
        # service that happens to be writing. Partial, because NULL means
        # "no source" and any number of manual uploads share that.
        Index(
            "uq_documents_user_id_source_uri",
            "user_id",
            "source_uri",
            unique=True,
            sqlite_where=text("source_uri IS NOT NULL"),
            postgresql_where=text("source_uri IS NOT NULL"),
        ),
    )


class DocumentChunk(Base):
    """A contiguous slice of a document's text, with its provenance.

    `embedding` is nullable and unpopulated by ingestion. The embedding feature
    fills it in place; no schema change is required at that point.
    """

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised from the parent so retrieval can filter by owner without a
    # join on the hot path.
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)

    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_doc_index"
        ),
    )
