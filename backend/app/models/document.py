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
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.settings import settings
from app.database.base import Base


class DocumentStatus(StrEnum):
    """Lifecycle of an uploaded document."""

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


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
