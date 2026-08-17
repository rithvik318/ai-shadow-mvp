import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SyncStatus(StrEnum):
    """The outcome of the most recent synchronisation of one source."""

    NEVER_RUN = "never_run"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    # Everything reachable was processed, but something transient failed — a
    # download that did not complete, a Graph call that errored. Distinct from
    # `failed` because the knowledge base did change, and distinct from
    # `succeeded` because the delta token was deliberately not advanced.
    PARTIAL = "partial"
    FAILED = "failed"


SyncStatusType = SAEnum(
    SyncStatus,
    name="sync_status",
    native_enum=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class OneDriveSyncState(Base):
    """Where synchronisation of one configured folder got to.

    One row per configured source, keyed by the `key` from configuration
    rather than by anything Graph supplies. That key is the only stable
    identity a source has: drive and item ids are discovered, and a folder
    that is re-pointed at a different path is still the same source as far as
    this system is concerned.

    The delta link is the whole point of the table. It is an opaque URL from
    Graph that encodes "everything up to here has been seen", and it is only
    written after the work it describes has actually been done — otherwise a
    sync that died halfway would resume from a point it never reached, and the
    files in between would never be indexed.
    """

    __tablename__ = "onedrive_sync_state"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    source_key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    label: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Resolved from configuration on first contact and then reused, so a later
    # rename of a parent folder does not silently point the sync somewhere new.
    drive_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    item_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    delta_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[SyncStatus] = mapped_column(
        SyncStatusType, nullable=False, default=SyncStatus.NEVER_RUN, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Attempted vs. succeeded are both kept: a source whose last attempt failed
    # still needs to report when it was last known good.
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # A summary of the last run, for the status endpoint. Counts only — no
    # filenames, so the row does not grow with the corpus.
    last_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_replaced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_unsupported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
