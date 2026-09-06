import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.sync import SyncStatus
from app.services.features.sync.onedrive_sync_service import SyncResult


class SyncRequest(BaseModel):
    """What to synchronise, and how hard."""

    source: str | None = Field(
        default=None,
        description="Configured source key. Omit to synchronise every source.",
    )
    full: bool = Field(
        default=False,
        description=(
            "Ignore the stored delta token and re-enumerate the folder. "
            "Safe to repeat: unchanged files are skipped, not duplicated."
        ),
    )


class SyncFileResult(BaseModel):
    """What synchronisation did about one file."""

    name: str
    source_uri: str
    result: SyncResult
    document_id: uuid.UUID | None = None
    reason: str | None = None


class SyncSourceSummary(BaseModel):
    """The result of synchronising one source."""

    source_key: str
    label: str
    mode: str = Field(description="`full` or `incremental`.")
    status: SyncStatus
    discovered: int
    indexed: int
    unchanged: int
    replaced: int
    deleted: int
    unsupported: int
    failed: int
    duration_seconds: float
    delta_advanced: bool = Field(
        description=(
            "Whether the delta token moved. False after a transient failure, "
            "so the next run re-examines the same window."
        )
    )
    error: str | None = None
    files: list[SyncFileResult]


class SyncResponse(BaseModel):
    """One entry per source synchronised."""

    sources: list[SyncSourceSummary]
    total_discovered: int
    total_indexed: int
    total_replaced: int
    total_unchanged: int
    total_deleted: int
    total_unsupported: int
    total_failed: int
    duration_seconds: float


class SyncStateResponse(BaseModel):
    """Where one source's synchronisation currently stands.

    No delta link and no credentials: the token is a bearer credential for the
    window it describes, and this endpoint is for people, not for resuming.
    """

    model_config = ConfigDict(from_attributes=True)

    source_key: str
    label: str | None
    drive_id: str | None
    item_id: str | None
    path: str | None = Field(
        default=None, description="The configured folder path, when addressed by path."
    )
    uri: str | None = Field(
        default=None,
        description=(
            "The folder's human-facing address, as configured. Never a Graph "
            "URL and never carries a token or a query string."
        ),
    )
    enabled: bool = Field(
        default=True, description="Whether this source takes part in a run."
    )
    configured: bool = Field(
        default=True,
        description=(
            "False for a source that has sync state but is no longer in "
            "ONEDRIVE_SOURCES — its documents stay indexed until removed."
        ),
    )
    status: SyncStatus
    has_delta_token: bool
    last_discovered: int = Field(
        default=0,
        description=(
            "Files the last run examined. Derived from the per-outcome counts "
            "rather than stored, so it cannot disagree with them."
        ),
    )
    error_message: str | None
    last_attempted_at: datetime | None
    last_succeeded_at: datetime | None
    last_duration_ms: int | None
    last_indexed: int
    last_replaced: int
    last_unchanged: int
    last_deleted: int
    last_unsupported: int
    last_failed: int


class SyncStatusResponse(BaseModel):
    """Sync state for every configured source, plus how it is scheduled."""

    configured: bool
    scheduled: bool
    interval_seconds: int | None
    configuration_error: str | None = Field(
        default=None,
        description=(
            "Why the configuration could not be read, when it could not be. "
            "Shown so a typo in ONEDRIVE_SOURCES does not present as an empty "
            "deployment."
        ),
    )
    sources: list[SyncStateResponse]
