"""Turning OneDrive changes into knowledge-base changes.

This service discovers what changed and decides what to do about it. It does
not parse, chunk, embed or store anything — every file it decides to index
goes through `ingestion_service.ingest_file`, the same function the upload
endpoints call. That is what makes "uploaded by hand" and "arrived from
OneDrive" the same document to everything downstream.

Two rules shape the rest of this module.

**One file's failure is that file's failure.** A batch that aborts on the
first unreadable PDF would leave the corpus in a state nobody can describe.

**The delta token is only advanced when it is safe to.** It is a promise that
everything before it has been dealt with. If a download failed — meaning the
file's content was never seen — that promise would be false, and the file
would never be looked at again until it happened to change. So a run with any
transient failure keeps the previous token and reports `partial`.
"""

import logging
import os
import tempfile
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import (
    DeltaTokenExpiredError,
    GraphError,
    SyncNotConfiguredError,
)
from app.models.document import IngestionResult
from app.models.sync import OneDriveSyncState, SyncStatus
from app.services.features.documents.document_service import delete_document_by_source
from app.services.features.documents.ingestion_service import ingest_file
from app.services.features.sync.source_config import (
    OneDriveSource,
    get_source,
    load_sources,
)
from app.services.graph import drive_service
from app.services.graph.client import GraphClient
from app.services.graph.drive_service import DriveItem

logger = logging.getLogger(__name__)


class SyncResult(StrEnum):
    """What synchronisation did about one file.

    The ingestion vocabulary plus `deleted`, which ingestion has no opinion
    about because an upload cannot express the absence of a file.
    """

    INDEXED = "indexed"
    UNCHANGED = "unchanged"
    REPLACED = "replaced"
    DELETED = "deleted"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


_FROM_INGESTION: dict[IngestionResult, SyncResult] = {
    IngestionResult.INDEXED: SyncResult.INDEXED,
    IngestionResult.UNCHANGED: SyncResult.UNCHANGED,
    IngestionResult.REPLACED: SyncResult.REPLACED,
    IngestionResult.UNSUPPORTED: SyncResult.UNSUPPORTED,
    IngestionResult.FAILED: SyncResult.FAILED,
}


@dataclass(frozen=True)
class FileOutcome:
    """What happened to one file, reported independently of the others."""

    name: str
    source_uri: str
    result: SyncResult
    document_id: uuid.UUID | None = None
    reason: str | None = None
    # A failure this system could plausibly succeed at next time — a download
    # that did not complete, Graph returning a 500. Distinct from a file that
    # will fail identically forever, such as a corrupt PDF, because only the
    # former is a reason to withhold the delta token.
    transient: bool = False


@dataclass
class SyncSummary:
    """The result of synchronising one source."""

    source_key: str
    label: str
    mode: str
    status: SyncStatus = SyncStatus.SUCCEEDED
    discovered: int = 0
    indexed: int = 0
    unchanged: int = 0
    replaced: int = 0
    deleted: int = 0
    unsupported: int = 0
    failed: int = 0
    duration_seconds: float = 0.0
    delta_advanced: bool = False
    error: str | None = None
    outcomes: list[FileOutcome] = field(default_factory=list)

    def record(self, outcome: FileOutcome) -> None:
        self.outcomes.append(outcome)
        setattr(self, outcome.result.value, getattr(self, outcome.result.value) + 1)


# --- state ---------------------------------------------------------------


def get_state(db: Session, source_key: str) -> OneDriveSyncState | None:
    return db.execute(
        select(OneDriveSyncState).where(OneDriveSyncState.source_key == source_key)
    ).scalar_one_or_none()


def list_states(db: Session) -> list[OneDriveSyncState]:
    return list(
        db.execute(select(OneDriveSyncState).order_by(OneDriveSyncState.source_key))
        .scalars()
        .all()
    )


def _get_or_create_state(db: Session, source: OneDriveSource) -> OneDriveSyncState:
    state = get_state(db, source.key)

    if state is None:
        state = OneDriveSyncState(
            source_key=source.key, label=source.label, status=SyncStatus.NEVER_RUN
        )
        db.add(state)
        db.flush()

    return state


# --- one file ------------------------------------------------------------


def _max_file_bytes() -> int:
    configured = settings.ONEDRIVE_MAX_FILE_BYTES

    return configured if configured is not None else settings.MAX_UPLOAD_SIZE_BYTES


def _download_to_staging(client: GraphClient, item: DriveItem) -> str:
    """Write a file's bytes to a temporary path and return it.

    Staged to disk rather than held in memory so that a large file does not
    have to fit in the process alongside everything else the request is doing.
    The caller is responsible for removing it, and does so in a `finally`.
    """

    url = item.download_url

    if not url:
        # Some payloads omit the pre-authorised URL; ask for it directly.
        payload = client.get(f"/drives/{item.drive_id}/items/{item.item_id}")
        url = payload.get("@microsoft.graph.downloadUrl")

    if not url:
        raise GraphError(f"Graph offered no download URL for {item.name!r}.")

    data = client.download(url)

    staging_dir = settings.ONEDRIVE_STAGING_DIR
    if staging_dir:
        os.makedirs(staging_dir, exist_ok=True)

    handle, path = tempfile.mkstemp(prefix="onedrive-", dir=staging_dir)
    try:
        with os.fdopen(handle, "wb") as staged:
            staged.write(data)
    except BaseException:
        os.unlink(path)
        raise

    return path


def sync_one_file(
    db: Session,
    client: GraphClient,
    item: DriveItem,
    *,
    user_id: str = MVP_USER_ID,
) -> FileOutcome:
    """Bring one OneDrive file into the knowledge base, and report what happened.

    Never raises for anything to do with this file. A synchronisation is a
    loop over files, and a loop that can be ended by any one of its items is
    not a synchronisation.
    """

    if item.deleted:
        removed = delete_document_by_source(db, item.source_uri, user_id=user_id)

        return FileOutcome(
            name=item.name,
            source_uri=item.source_uri,
            result=SyncResult.DELETED,
            reason=None if removed else "No indexed document for this source.",
        )

    if item.size is not None and item.size > _max_file_bytes():
        return FileOutcome(
            name=item.name,
            source_uri=item.source_uri,
            result=SyncResult.UNSUPPORTED,
            reason=(
                f"File is {item.size} bytes, above the "
                f"{_max_file_bytes()}-byte limit, and was not downloaded."
            ),
        )

    try:
        staged_path = _download_to_staging(client, item)
    except GraphError as exc:
        # Transient: the content was never seen, so the delta token must not
        # move past it.
        return FileOutcome(
            name=item.name,
            source_uri=item.source_uri,
            result=SyncResult.FAILED,
            reason=str(exc),
            transient=True,
        )

    try:
        with open(staged_path, "rb") as staged:
            data = staged.read()

        outcome = ingest_file(
            db,
            data=data,
            filename=item.name,
            content_type=item.mime_type,
            user_id=user_id,
            source_uri=item.source_uri,
            source_version=item.version,
        )
    except Exception as exc:  # noqa: BLE001 - one file must not end the sync
        logger.exception("onedrive_file_failed", extra={"file": item.name})

        return FileOutcome(
            name=item.name,
            source_uri=item.source_uri,
            result=SyncResult.FAILED,
            reason=f"Ingestion failed unexpectedly ({type(exc).__name__}).",
            transient=True,
        )
    finally:
        # Nothing is kept. This runs whether ingestion succeeded, failed, or
        # raised something nobody anticipated — a sync that leaves its
        # downloads behind is a mirror of OneDrive by accident.
        if os.path.exists(staged_path):
            os.unlink(staged_path)

    return FileOutcome(
        name=item.name,
        source_uri=item.source_uri,
        result=_FROM_INGESTION.get(outcome.result, SyncResult.FAILED),
        document_id=outcome.document_id,
        reason=outcome.reason,
    )


# --- one source ----------------------------------------------------------


def _discover(
    client: GraphClient,
    state: OneDriveSyncState,
    *,
    drive_id: str,
    item_id: str,
    full: bool,
) -> tuple[list[DriveItem], str | None, str]:
    """Return the items to process, the next delta link, and the mode used."""

    if not full and state.delta_link:
        try:
            changes, next_link = drive_service.iter_delta(
                client, drive_id=drive_id, item_id=item_id, delta_link=state.delta_link
            )
            return changes, next_link, "incremental"
        except DeltaTokenExpiredError:
            # Graph will not answer incrementally any more. Falling back is
            # correct rather than exceptional: a full pass is idempotent, so
            # the cost is time, not duplicated documents.
            logger.warning(
                "onedrive_delta_token_expired", extra={"source": state.source_key}
            )

    changes, next_link = drive_service.iter_delta(
        client, drive_id=drive_id, item_id=item_id, delta_link=None
    )

    return changes, next_link, "full"


def sync_source(
    db: Session,
    client: GraphClient,
    source: OneDriveSource,
    *,
    full: bool = False,
    user_id: str = MVP_USER_ID,
) -> SyncSummary:
    """Synchronise one configured folder and report per-file results."""

    started = time.perf_counter()
    state = _get_or_create_state(db, source)

    state.label = source.label
    state.status = SyncStatus.RUNNING
    state.last_attempted_at = datetime.now(UTC)
    db.commit()

    summary = SyncSummary(source_key=source.key, label=source.label, mode="full")

    try:
        # Resolve once, then trust the stored ids. A path is only correct
        # until somebody renames a parent folder, and re-resolving one every
        # run would quietly follow that rename to a different folder.
        if state.drive_id and state.item_id:
            drive_id, item_id = state.drive_id, state.item_id
        else:
            folder = drive_service.resolve_folder(
                client,
                drive_id=source.drive_id,
                path=source.path,
                item_id=source.item_id,
            )
            drive_id, item_id = folder.drive_id, folder.item_id
            state.drive_id, state.item_id = drive_id, item_id
            db.commit()

        changes, next_link, mode = _discover(
            client, state, drive_id=drive_id, item_id=item_id, full=full
        )
    except GraphError as exc:
        # Nothing was processed, so nothing about the stored state is stale.
        # It is left exactly as it was, including the delta link.
        state.status = SyncStatus.FAILED
        state.error_message = str(exc)
        state.last_duration_ms = int((time.perf_counter() - started) * 1000)
        db.commit()

        summary.status = SyncStatus.FAILED
        summary.error = str(exc)
        summary.duration_seconds = round(time.perf_counter() - started, 3)

        return summary

    summary.mode = mode

    files = [item for item in changes if not item.is_folder]
    summary.discovered = len(files)

    for item in files:
        summary.record(sync_one_file(db, client, item, user_id=user_id))

    transient = sum(1 for outcome in summary.outcomes if outcome.transient)

    # The rule this whole module is arranged around. A file whose content was
    # never fetched has not been dealt with, and a token saying otherwise
    # would bury it until it changed again of its own accord.
    if transient == 0 and next_link:
        state.delta_link = next_link
        summary.delta_advanced = True

    summary.status = SyncStatus.PARTIAL if transient else SyncStatus.SUCCEEDED
    summary.duration_seconds = round(time.perf_counter() - started, 3)

    state.status = summary.status
    state.error_message = (
        f"{transient} file(s) failed transiently; delta token not advanced."
        if transient
        else None
    )
    state.last_duration_ms = int(summary.duration_seconds * 1000)
    state.last_indexed = summary.indexed
    state.last_replaced = summary.replaced
    state.last_unchanged = summary.unchanged
    state.last_deleted = summary.deleted
    state.last_unsupported = summary.unsupported
    state.last_failed = summary.failed

    if summary.status is SyncStatus.SUCCEEDED:
        state.last_succeeded_at = datetime.now(UTC)

    db.commit()

    logger.info(
        "onedrive_source_synced",
        extra={
            "source": source.key,
            "mode": mode,
            "discovered": summary.discovered,
            "indexed": summary.indexed,
            "replaced": summary.replaced,
            "deleted": summary.deleted,
            "failed": summary.failed,
            "delta_advanced": summary.delta_advanced,
        },
    )

    return summary


# --- every source --------------------------------------------------------


def sync_all(
    db: Session,
    *,
    client: GraphClient | None = None,
    source_key: str | None = None,
    full: bool = False,
    user_id: str = MVP_USER_ID,
) -> list[SyncSummary]:
    """Synchronise every configured source, or one named source.

    The entry point both the API and the scheduler call. `client` is injected
    by tests; a real caller lets it be built from configuration, which is also
    where "not configured" is turned into an error worth reading.
    """

    sources: Iterable[OneDriveSource]

    if source_key:
        sources = [get_source(source_key)]
    else:
        sources = load_sources()

        if not sources:
            raise SyncNotConfiguredError(
                "No OneDrive sources are configured. Set ONEDRIVE_SOURCES to a "
                "JSON array of folders to synchronise."
            )

    graph = client or GraphClient.from_settings()

    return [
        sync_source(db, graph, source, full=full, user_id=user_id) for source in sources
    ]
