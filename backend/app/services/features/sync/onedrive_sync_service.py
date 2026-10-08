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
everything before it has been dealt with. If a download failed *transiently* —
meaning the file's content was never seen and might be next time — that promise
would be false, so the previous token is kept and the run reports `partial`.

**A permanent failure is dealt with.** This is the half the first version got
wrong, and it is what turned a slow run into a stuck one. A legacy `.doc`, a
corrupt PDF, an item the application will never be allowed to open: none of
these will read differently tomorrow, so a token held back for them is held back
forever, the whole folder is re-enumerated on every run, and the corpus never
finishes arriving. Permanent failures are recorded against the file and the
token moves on.

**Work is arranged to avoid downloads, not to speed them up.** A file over the
size ceiling, or in a format this application does not parse, is decided from
the folder listing — no bytes are fetched for a rejection the filename already
settled. What remains is downloaded a batch at a time across a small thread
pool, and ingested serially on the calling thread: downloads touch the network
and a staging file, while everything after them touches a `Session` that is not
thread-safe.
"""

import logging
import os
import tempfile
import time
import uuid
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import (
    DeltaTokenExpiredError,
    GraphError,
    SourceUnresolvableError,
    SyncAlreadyRunningError,
    SyncNotConfiguredError,
    UnsupportedDocumentTypeError,
)
from app.models.document import IngestionResult
from app.models.sync import OneDriveSyncState, SyncStatus
from app.services.features.documents.document_service import delete_document_by_source
from app.services.features.documents.ingestion_service import (
    ingest_file,
    record_failed_source,
    record_unsupported_source,
)
from app.services.features.documents.parser_service import resolve_format
from app.services.features.sync import source_resolver
from app.services.features.sync.source_config import (
    OneDriveSource,
    enabled_sources,
    get_source,
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
    #: Read successfully, holds no text layer. Not a failure and not an
    #: unsupported format — see `DocumentStatus.OCR_REQUIRED`.
    OCR_REQUIRED = "ocr_required"
    FAILED = "failed"


_FROM_INGESTION: dict[IngestionResult, SyncResult] = {
    IngestionResult.INDEXED: SyncResult.INDEXED,
    IngestionResult.UNCHANGED: SyncResult.UNCHANGED,
    IngestionResult.REPLACED: SyncResult.REPLACED,
    IngestionResult.UNSUPPORTED: SyncResult.UNSUPPORTED,
    IngestionResult.OCR_REQUIRED: SyncResult.OCR_REQUIRED,
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
    #: Whether this file's bytes were actually fetched. False for a download
    #: failure, and the difference matters: a corrupt PDF was read perfectly
    #: well and simply will not parse, while a file that never downloaded says
    #: nothing about itself at all. Only the second kind can indicate that the
    #: downloader — rather than the corpus — is what is broken.
    downloaded: bool = True


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
    #: Files whose bytes were read and which carry no text. Counted here rather
    #: than in `failed` so that a corpus of scans does not make a healthy run
    #: look broken, and so the backlog has a number of its own.
    ocr_required: int = 0
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


# A run that outlives this is not running: the process that claimed it died
# without clearing the flag, and refusing every future sync because of a crash
# would be worse than the double-run the flag exists to prevent.
_STALE_RUN_AFTER = timedelta(hours=6)


def _is_stale(state: OneDriveSyncState) -> bool:
    if state.last_attempted_at is None:
        return True

    attempted = state.last_attempted_at
    if attempted.tzinfo is None:
        # SQLite hands back naive datetimes; Postgres does not.
        attempted = attempted.replace(tzinfo=UTC)

    return datetime.now(UTC) - attempted > _STALE_RUN_AFTER


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

    The URL carried on the item is tried first and is allowed to be stale: it
    was minted when the folder was enumerated, and Graph gives these about an
    hour. `refresh` is what makes that survivable — the download asks for a
    current URL once, rather than recording a file whose bytes are perfectly
    readable as a failure because the run took longer than an hour.

    Called from a worker thread. It touches the network and the staging
    directory and nothing else — no session, no model, no settings write — which
    is the property that makes overlapping downloads safe.
    """

    def refresh() -> str:
        return drive_service.fresh_download_url(
            client, drive_id=item.drive_id, item_id=item.item_id
        )

    url = item.download_url or refresh()
    data = client.download(url, refresh=refresh)

    staging_dir = settings.ONEDRIVE_STAGING_DIR
    if staging_dir:
        os.makedirs(staging_dir, exist_ok=True)

    handle, path = tempfile.mkstemp(prefix="onedrive-", dir=staging_dir)
    try:
        with os.fdopen(handle, "wb") as staged:
            staged.write(data)
    except BaseException:
        # `os.fdopen` takes ownership of the descriptor, and the `with` closes
        # it on the way out — but only if `fdopen` itself got that far. If it
        # did not, the descriptor is still open, and on Windows an open
        # descriptor makes the file undeletable: the unlink below would raise
        # `PermissionError` on top of the error being handled, and the staged
        # file would be left behind.
        try:
            os.close(handle)
        except OSError:
            pass

        os.unlink(path)
        raise

    return path


def classify_without_downloading(item: DriveItem) -> str | None:
    """Why this file cannot be indexed, decided from its metadata alone.

    None means "fetch it and find out". Anything else is a reason, and the file
    is never downloaded.

    Both questions this answers are settled by the listing: a size against the
    ceiling, and a format against the name and the declared type. Asking them
    here rather than after the download is what turns a third of a real corpus
    from bytes-over-the-wire into a row. In the SunRadia folder that is 292 of
    847 files — a third of the run's downloads spent to reach a rejection that
    the filename had already decided.
    """

    if item.size is not None and item.size > _max_file_bytes():
        return (
            f"File is {item.size} bytes, above the {_max_file_bytes()}-byte "
            "limit, and was not downloaded."
        )

    try:
        resolve_format(item.name, item.mime_type)
    except UnsupportedDocumentTypeError as exc:
        return str(exc)

    return None


def _unsupported(
    db: Session, item: DriveItem, reason: str, *, user_id: str
) -> FileOutcome:
    """Record a file that will never be indexed, without fetching it."""

    outcome = record_unsupported_source(
        db,
        filename=item.name,
        content_type=item.mime_type,
        user_id=user_id,
        source_uri=item.source_uri,
        source_version=item.version,
        reason=reason,
        size_bytes=item.size or 0,
    )

    return FileOutcome(
        name=item.name,
        source_uri=item.source_uri,
        result=SyncResult.UNSUPPORTED,
        document_id=outcome.document_id,
        reason=reason,
    )


def ingest_staged_file(
    db: Session,
    item: DriveItem,
    staged_path: str,
    *,
    user_id: str = MVP_USER_ID,
) -> FileOutcome:
    """Ingest one already-downloaded file and report what happened.

    Split from the download so the two can run at different concurrencies: this
    half writes to the database and stays on one thread with one session.

    Never raises. `ingest_file` already turns every document error into an
    outcome, so the guard below catches only what nobody anticipated — and that
    is recorded as **permanent**. A bug in this process will reproduce itself
    identically on the next run, so treating it as transient would withhold the
    delta token forever and re-walk the whole folder nightly to collect the same
    exception.
    """

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
            transient=False,
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
        # A parse failure, an empty document, an embedding refusal — every one
        # of these is a property of the file and will happen again identically.
        # None of them is a reason to hold the delta token back.
        transient=False,
    )


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

    Kept as the one-file-at-a-time path: the batched loop below is what a real
    sync uses, and this is what a caller with a single item wants.
    """

    if item.deleted:
        return delete_one_file(db, item, user_id=user_id)

    reason = classify_without_downloading(item)

    if reason is not None:
        return _unsupported(db, item, reason, user_id=user_id)

    try:
        staged_path = _download_to_staging(client, item)
    except GraphError as exc:
        return _download_failure(db, item, exc, user_id=user_id)

    return ingest_staged_file(db, item, staged_path, user_id=user_id)


def delete_one_file(
    db: Session, item: DriveItem, *, user_id: str = MVP_USER_ID
) -> FileOutcome:
    """Withdraw a document whose file is gone from the drive."""

    removed = delete_document_by_source(db, item.source_uri, user_id=user_id)

    return FileOutcome(
        name=item.name,
        source_uri=item.source_uri,
        result=SyncResult.DELETED,
        reason=None if removed else "No indexed document for this source.",
    )


def _download_failure(
    db: Session, item: DriveItem, exc: GraphError, *, user_id: str = MVP_USER_ID
) -> FileOutcome:
    """Turn a failed download into an outcome, and leave a row behind.

    `exc.transient` is the whole point of the classification. It used to be
    hard-coded `True`, which meant a file the application will never be allowed
    to read held the delta token back on every run, forever.

    The row is the other half, and it was missing. A download failure recorded
    only a count, so a run reporting 487 failures could not say which files or
    why — not from the database, not from the status endpoint, not afterwards at
    all. Now each one is a `failed` document carrying the filename, the declared
    type, the reported size and Graph's own reason, which makes "counts by
    extension, by MIME type, by reason" an ordinary query.
    """

    reason = str(exc)

    try:
        outcome = record_failed_source(
            db,
            filename=item.name,
            content_type=item.mime_type,
            user_id=user_id,
            source_uri=item.source_uri,
            source_version=item.version,
            reason=reason,
            size_bytes=item.size or 0,
        )
        document_id = outcome.document_id
    except Exception:  # noqa: BLE001 - recording must not replace the real error
        logger.exception("onedrive_failure_not_recorded", extra={"file": item.name})
        db.rollback()
        document_id = None

    return FileOutcome(
        name=item.name,
        source_uri=item.source_uri,
        result=SyncResult.FAILED,
        document_id=document_id,
        reason=reason,
        transient=exc.transient,
        downloaded=False,
    )


# --- many files ----------------------------------------------------------


def _batched(items: list[DriveItem], size: int) -> Iterator[list[DriveItem]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _process(
    db: Session,
    client: GraphClient,
    files: list[DriveItem],
    *,
    user_id: str = MVP_USER_ID,
) -> Iterator[FileOutcome]:
    """Work through every file: classify, download in parallel, ingest in order.

    Three passes over each batch, in an order chosen to make the expensive one
    as small as possible.

    **Classify first, from metadata.** A deletion needs no download; nor does a
    file over the size ceiling, nor one whose extension this application does
    not parse. Deciding that before the network is touched is what removed a
    third of the downloads from the SunRadia corpus.

    **Download the rest concurrently.** Downloads are the only stage that is
    safe to overlap — network and a staging file, no session — and they are what
    the run is otherwise waiting on. The pool is small on purpose: Graph
    throttles a first full sync hard, and the failure mode of too much
    concurrency is a 429 storm that ends up slower than serial.

    **Ingest serially, on this thread, with this session.** Parsing, chunking,
    embedding and every write stay here. `Session` is not thread-safe, and
    handing one to a pool is a corruption bug that only shows up under load —
    exactly the load a 847-file sync produces.

    Batching bounds two things that would otherwise grow with the corpus: how
    much staging disk is in use at once, and how long a pre-authorised download
    URL waits before it is spent. The second is the one that mattered — a URL
    minted during enumeration and used twenty hours later is expired, and that
    is what the last run recorded as four hundred failures.
    """

    workers = max(1, settings.ONEDRIVE_DOWNLOAD_CONCURRENCY)
    batch_size = max(1, settings.ONEDRIVE_DOWNLOAD_BATCH_SIZE)

    for batch in _batched(files, batch_size):
        pending: list[DriveItem] = []

        for item in batch:
            if item.deleted:
                yield delete_one_file(db, item, user_id=user_id)
                continue

            reason = classify_without_downloading(item)

            if reason is not None:
                yield _unsupported(db, item, reason, user_id=user_id)
                continue

            pending.append(item)

        if not pending:
            continue

        staged: dict[str, str | GraphError] = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_download_to_staging, client, item): item
                for item in pending
            }

            for future in as_completed(futures):
                item = futures[future]

                try:
                    staged[item.item_id] = future.result()
                except GraphError as exc:
                    staged[item.item_id] = exc
                except Exception as exc:  # noqa: BLE001 - one file, not the run
                    # A staging write that failed, most likely. Transient by
                    # default: the bytes were never seen, so the token must not
                    # move past this file.
                    logger.exception(
                        "onedrive_download_failed", extra={"file": item.name}
                    )
                    staged[item.item_id] = GraphError(
                        f"Download failed unexpectedly ({type(exc).__name__})."
                    )

        # Back on one thread, in the order the folder listed them, so a run's
        # log reads the way the folder does.
        for item in pending:
            result = staged.get(item.item_id)

            if isinstance(result, GraphError):
                yield _download_failure(db, item, result, user_id=user_id)
            elif result is None:
                yield _download_failure(
                    db,
                    item,
                    GraphError(f"No download was attempted for {item.name!r}."),
                    user_id=user_id,
                )
            else:
                yield ingest_staged_file(db, item, result, user_id=user_id)


def _abandon(
    db: Session,
    source: OneDriveSource,
    summary: SyncSummary,
    exc: BaseException,
    *,
    started: float,
) -> SyncSummary:
    """Release a source whose run died, and report what was done before it did.

    Three things have to be true afterwards, and the first is the one that
    matters: the source must not be left saying `running`. A run that raises
    after claiming the flag blocks every later run until `_is_stale` forgives
    it six hours on, which turns one unwritable row into a morning of refusals.

    The delta token is deliberately not touched. The run did not finish, so the
    promise the token makes — everything before this point has been dealt
    with — would be false.

    The counts gathered before the failure are kept. Files already ingested
    were committed one at a time, so they really are in the corpus, and
    reporting zero would misdescribe the database.
    """

    logger.exception("onedrive_source_aborted", extra={"source": source.key})

    # The failing statement poisoned the transaction, so nothing can be written
    # until it is rolled back — including the release below.
    db.rollback()

    summary.status = SyncStatus.FAILED
    summary.duration_seconds = round(time.perf_counter() - started, 3)
    summary.error = (
        f"Synchronisation stopped after {len(summary.outcomes)} of "
        f"{summary.discovered} file(s) ({type(exc).__name__}). The delta token "
        "was not advanced, so the next run starts again from where this one "
        "began."
    )

    # Re-read rather than reuse: the rollback expired the instance that was
    # loaded inside the dead transaction.
    state = _get_or_create_state(db, source)
    state.status = summary.status
    state.error_message = summary.error
    state.last_duration_ms = int(summary.duration_seconds * 1000)
    state.last_indexed = summary.indexed
    state.last_replaced = summary.replaced
    state.last_unchanged = summary.unchanged
    state.last_deleted = summary.deleted
    state.last_unsupported = summary.unsupported
    state.last_ocr_required = summary.ocr_required
    state.last_failed = summary.failed
    db.commit()

    return summary


def _describe_failures(failures: list[FileOutcome], *, blind: bool) -> str:
    """Say how many files failed, how they split, and what they said.

    Two earlier versions of this were not enough to act on. The first said only
    "405 file(s) failed transiently", which cannot distinguish an expired
    download URL from a throttled one from a folder nobody has consented to —
    three different remedies. The second described transient failures only, so a
    run whose failures were all *permanent* persisted no reason at all and
    reported itself as a success.

    Permanent and transient are counted separately because they are different
    questions: transient asks "will the next run fix it", permanent asks "is
    this file ever coming".
    """

    transient = [item for item in failures if item.transient]
    permanent = [item for item in failures if not item.transient]

    counts: dict[str, int] = {}

    for outcome in failures:
        reason = (outcome.reason or "unknown").split(";")[0].strip()
        counts[reason] = counts.get(reason, 0) + 1

    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    detail = "; ".join(f"{reason} ({count})" for reason, count in ranked[:5])

    lead = (
        f"{len(failures)} file(s) failed — {len(transient)} transient, "
        f"{len(permanent)} permanent."
    )

    if blind:
        lead += (
            " Nothing was read successfully, so the delta token was held back: "
            "every download failing is better explained by a broken download "
            "path than by that many independently unreadable files."
        )

    return f"{lead} Causes: {detail}"


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

    # Two runs over one source would download the same files twice and race to
    # write the same delta token — and the loser's token would describe work
    # the winner never did. The scheduler and a manual API call can both land
    # here, so the claim is made in the database rather than in process memory.
    if state.status is SyncStatus.RUNNING and not _is_stale(state):
        raise SyncAlreadyRunningError(
            f"Source {source.key!r} is already synchronising. Wait for it to "
            "finish, or retry once it has."
        )

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
            # One resolver for all three ways a folder can be named — a path,
            # an item id, or a sharing link. A share-linked folder therefore
            # needs no manual pinning step before its first sync, and nothing
            # below this line knows which route it arrived by.
            resolved = source_resolver.resolve_source(client, source)

            if not resolved.resolved:
                raise SourceUnresolvableError(
                    resolved.error or "The source could not be resolved.",
                    remedy=resolved.remedy,
                )

            drive_id = str(resolved.drive_id)
            item_id = str(resolved.item_id)
            state.drive_id, state.item_id = drive_id, item_id
            db.commit()

        changes, next_link, mode = _discover(
            client, state, drive_id=drive_id, item_id=item_id, full=full
        )
    except Exception as exc:  # noqa: BLE001 - see below
        # Nothing was processed, so nothing about the stored state is stale.
        # It is left exactly as it was, including the delta link.
        #
        # Broader than `GraphError` on purpose. Whatever went wrong, the row
        # was already marked `running`, and leaving it that way would block
        # every future run of this source for six hours over a bug. A Graph
        # error is safe to repeat verbatim — the client strips query strings —
        # but anything else is reported by type only, so a stack trace or a
        # URL never reaches a caller.
        message = (
            str(exc)
            if isinstance(exc, GraphError | SourceUnresolvableError)
            else f"Synchronisation failed unexpectedly ({type(exc).__name__})."
        )

        if not isinstance(exc, GraphError | SourceUnresolvableError):
            logger.exception("onedrive_source_failed", extra={"source": source.key})

        state.status = SyncStatus.FAILED
        state.error_message = message
        state.last_duration_ms = int((time.perf_counter() - started) * 1000)
        db.commit()

        summary.status = SyncStatus.FAILED
        summary.error = message
        summary.duration_seconds = round(time.perf_counter() - started, 3)

        return summary

    summary.mode = mode

    files = [item for item in changes if not item.is_folder]
    summary.discovered = len(files)

    try:
        for outcome in _process(db, client, files, user_id=user_id):
            summary.record(outcome)
    except Exception as exc:  # noqa: BLE001 - the source must still be released
        # Whatever the per-file guards did not catch. In practice that means a
        # database error, which is different in kind from a file error: it
        # poisons the transaction rather than one item, so the loop cannot
        # simply continue.
        #
        # **The point of catching it here is the `running` flag.** It was
        # committed before the work began, so an exception escaping this
        # function leaves the row saying `running` — and `db.rollback()` in
        # `sync_all` cannot undo a committed write. The source then refuses
        # every sync for the six hours it takes `_is_stale` to forgive it.
        # That is exactly what a 6.8 GB file did through
        # `NumericValueOutOfRange`: one unwritable row, and the folder was
        # locked out for the rest of the morning.
        return _abandon(db, source, summary, exc, started=started)

    transient = [outcome for outcome in summary.outcomes if outcome.transient]

    # The rule this whole module is arranged around, now that `transient` means
    # what it says. A file whose content was never fetched has not been dealt
    # with, and a token saying otherwise would bury it until it changed again of
    # its own accord.
    #
    # The converse matters just as much and was the defect that cost a 25-hour
    # run: a file that is *permanently* unreadable — a legacy format, a corrupt
    # PDF, an item the application is not allowed to open — has been dealt with
    # as fully as it ever will be. Holding the token back for it means the next
    # run re-enumerates the entire folder and reaches the same conclusion, every
    # time, and the corpus never finishes arriving.
    # **A run that read nothing has proved nothing.** This guard is the second
    # half of the rule, and its absence let a live run over 859 files end with
    # 0 indexed, 487 permanent failures, `succeeded`, and a token advanced past
    # every file it had failed to read — burying the entire corpus, because a
    # delta only ever returns what changed *after* the token.
    #
    # The classification was not wrong in itself; it was being trusted in a
    # situation where it could not be. Every download failing is far better
    # explained by the downloader being broken than by 487 files each being
    # independently unreadable, so the token stays put and the next run gets
    # another look at them.
    unfetched = [
        item
        for item in summary.outcomes
        if item.result is SyncResult.FAILED and not item.downloaded
    ]
    fetched = sum(
        1
        for item in summary.outcomes
        if item.result
        in {SyncResult.INDEXED, SyncResult.REPLACED, SyncResult.UNCHANGED}
        or (item.result is SyncResult.FAILED and item.downloaded)
    )
    # Precisely: downloads failed and *not one* succeeded. A corrupt PDF is not
    # evidence of a broken downloader — its bytes arrived fine and simply will
    # not parse — so it counts as fetched.
    blind_run = bool(unfetched) and fetched == 0

    if not transient and not blind_run and next_link:
        state.delta_link = next_link
        summary.delta_advanced = True

    # `succeeded` means every discovered file was dealt with. A run that failed
    # files is `partial` whether those failures were transient or permanent —
    # reporting otherwise is how 487 unread files came to be described as a
    # success.
    summary.status = SyncStatus.SUCCEEDED if not summary.failed else SyncStatus.PARTIAL
    summary.duration_seconds = round(time.perf_counter() - started, 3)

    failures = [item for item in summary.outcomes if item.result is SyncResult.FAILED]

    state.status = summary.status
    state.error_message = (
        _describe_failures(failures, blind=blind_run) if failures else None
    )
    state.last_duration_ms = int(summary.duration_seconds * 1000)
    state.last_indexed = summary.indexed
    state.last_replaced = summary.replaced
    state.last_unchanged = summary.unchanged
    state.last_deleted = summary.deleted
    state.last_unsupported = summary.unsupported
    state.last_ocr_required = summary.ocr_required
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
        named = get_source(source_key)

        if not named.enabled:
            # Naming a disabled source is almost always a surprise rather than
            # an override, and syncing it would contradict the configuration
            # the deployment is running.
            raise SyncNotConfiguredError(
                f'Source {named.key!r} is disabled. Set "enabled": true in '
                "ONEDRIVE_SOURCES to synchronise it."
            )

        sources = [named]
    else:
        # Disabled sources are configuration, not work.
        sources = enabled_sources()

        if not sources:
            raise SyncNotConfiguredError(
                "No OneDrive sources are configured. Set ONEDRIVE_SOURCES to a "
                "JSON array of folders to synchronise."
            )

    graph = client or GraphClient.from_settings()

    summaries: list[SyncSummary] = []

    for source in sources:
        try:
            summaries.append(sync_source(db, graph, source, full=full, user_id=user_id))
        except SyncAlreadyRunningError as exc:
            # Reported rather than raised: one busy source must not stop the
            # other four from synchronising.
            summaries.append(
                SyncSummary(
                    source_key=source.key,
                    label=source.label,
                    mode="skipped",
                    status=SyncStatus.RUNNING,
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolation is the whole point
            # `sync_source` already turns everything it can into a summary.
            # This catches what it cannot — a database error while claiming the
            # run, say — so that one broken source still does not decide the
            # fate of the other four.
            logger.exception("onedrive_source_aborted", extra={"source": source.key})

            db.rollback()

            summaries.append(
                SyncSummary(
                    source_key=source.key,
                    label=source.label,
                    mode="aborted",
                    status=SyncStatus.FAILED,
                    error=f"Synchronisation aborted ({type(exc).__name__}).",
                )
            )

    return summaries
