from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import SyncError
from app.database.session import get_db
from app.models.sync import OneDriveSyncState, SyncStatus
from app.schemas.document_schema import ErrorResponse
from app.schemas.sync_schema import (
    SyncFileResult,
    SyncRequest,
    SyncResponse,
    SyncSourceSummary,
    SyncStateResponse,
    SyncStatusResponse,
)
from app.services.features.sync import onedrive_sync_service
from app.services.features.sync.source_config import OneDriveSource, load_sources

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post(
    "/onedrive",
    response_model=SyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Synchronise configured OneDrive folders into the knowledge base",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown source key"},
        409: {"model": ErrorResponse, "description": "Synchronisation not configured"},
        502: {"model": ErrorResponse, "description": "Microsoft Graph is unreachable"},
    },
)
def sync_onedrive(
    request: SyncRequest | None = None,
    db: Session = Depends(get_db),
) -> SyncResponse:
    """Run a synchronisation and report what changed, per file and in total.

    Incremental by default: only what Graph says has changed since the stored
    delta token is examined. Pass `full` to re-enumerate a folder — which is
    safe to repeat, because a file whose content has not changed is skipped
    rather than re-indexed.

    Synchronous, like ingestion, so the response describes finished work
    rather than a queued job. A first run over a large folder will take as
    long as it takes to download and embed it.
    """

    options = request or SyncRequest()

    summaries = onedrive_sync_service.sync_all(
        db, source_key=options.source, full=options.full
    )

    sources = [
        SyncSourceSummary(
            source_key=summary.source_key,
            label=summary.label,
            mode=summary.mode,
            status=summary.status,
            discovered=summary.discovered,
            indexed=summary.indexed,
            unchanged=summary.unchanged,
            replaced=summary.replaced,
            deleted=summary.deleted,
            unsupported=summary.unsupported,
            failed=summary.failed,
            duration_seconds=summary.duration_seconds,
            delta_advanced=summary.delta_advanced,
            error=summary.error,
            files=[
                SyncFileResult(
                    name=outcome.name,
                    source_uri=outcome.source_uri,
                    result=outcome.result,
                    document_id=outcome.document_id,
                    reason=outcome.reason,
                )
                for outcome in summary.outcomes
            ],
        )
        for summary in summaries
    ]

    def total(field: str) -> int:
        return sum(getattr(summary, field) for summary in summaries)

    return SyncResponse(
        sources=sources,
        total_discovered=total("discovered"),
        total_indexed=total("indexed"),
        total_replaced=total("replaced"),
        total_unchanged=total("unchanged"),
        total_deleted=total("deleted"),
        total_unsupported=total("unsupported"),
        total_failed=total("failed"),
        duration_seconds=round(total("duration_seconds"), 3),
    )


def _key_of(state: OneDriveSyncState) -> str:
    return state.source_key


def _state_response(
    source: OneDriveSource | None, state: OneDriveSyncState | None
) -> SyncStateResponse:
    """One row of the status table, from configuration, state, or both.

    Either half can be missing. A source configured this morning has no state
    and is `never_run`; a source deleted from configuration has state and no
    entry. Both are worth showing, and neither is an error.
    """

    if source is None and state is None:  # pragma: no cover - callers pass one
        raise ValueError("A status row needs a configured source or stored state.")

    key = source.key if source else state.source_key  # type: ignore[union-attr]

    if state is None:
        return SyncStateResponse(
            source_key=key,
            label=source.label,  # type: ignore[union-attr]
            drive_id=source.drive_id,  # type: ignore[union-attr]
            item_id=source.item_id,  # type: ignore[union-attr]
            path=source.path,  # type: ignore[union-attr]
            uri=source.uri,  # type: ignore[union-attr]
            enabled=source.enabled,  # type: ignore[union-attr]
            configured=True,
            status=SyncStatus.NEVER_RUN,
            has_delta_token=False,
            error_message=None,
            last_attempted_at=None,
            last_succeeded_at=None,
            last_duration_ms=None,
            last_discovered=0,
            last_indexed=0,
            last_replaced=0,
            last_unchanged=0,
            last_deleted=0,
            last_unsupported=0,
            last_failed=0,
        )

    return SyncStateResponse(
        source_key=key,
        label=(source.label if source else state.label),
        # The ids Graph actually answered to win over the configured ones:
        # they are what the next run will use.
        drive_id=state.drive_id or (source.drive_id if source else None),
        item_id=state.item_id or (source.item_id if source else None),
        path=source.path if source else None,
        uri=source.uri if source else None,
        enabled=source.enabled if source else False,
        configured=source is not None,
        status=state.status,
        has_delta_token=bool(state.delta_link),
        error_message=state.error_message,
        last_attempted_at=state.last_attempted_at,
        last_succeeded_at=state.last_succeeded_at,
        last_duration_ms=state.last_duration_ms,
        last_discovered=(
            state.last_indexed
            + state.last_replaced
            + state.last_unchanged
            + state.last_deleted
            + state.last_unsupported
            + state.last_failed
        ),
        last_indexed=state.last_indexed,
        last_replaced=state.last_replaced,
        last_unchanged=state.last_unchanged,
        last_deleted=state.last_deleted,
        last_unsupported=state.last_unsupported,
        last_failed=state.last_failed,
    )


@router.get(
    "/onedrive/status",
    response_model=SyncStatusResponse,
    summary="Where each configured OneDrive source's synchronisation stands",
)
def sync_status(db: Session = Depends(get_db)) -> SyncStatusResponse:
    """Report stored sync state without contacting Graph.

    Never returns the delta token itself — only whether one is held. The token
    is a bearer credential for the window it describes, and a status endpoint
    is for people reading it, not for resuming a sync.
    """

    sources: list[OneDriveSource] = []
    configuration_error: str | None = None

    try:
        sources = load_sources()
    except SyncError as exc:
        # A malformed configuration reads as "not configured" — but it says
        # why. Silently reporting zero sources is how a typo survives a week.
        configuration_error = str(exc)

    states = {
        state.source_key: state for state in onedrive_sync_service.list_states(db)
    }

    entries = [
        _state_response(source, states.pop(source.key, None)) for source in sources
    ]

    # Anything left has state but no configuration: a source that was removed
    # from ONEDRIVE_SOURCES. Its documents are still in the knowledge base, so
    # it is reported rather than hidden.
    entries.extend(
        _state_response(None, state) for state in sorted(states.values(), key=_key_of)
    )

    return SyncStatusResponse(
        configured=bool(sources),
        scheduled=settings.ONEDRIVE_SYNC_ENABLED,
        interval_seconds=(
            settings.ONEDRIVE_SYNC_INTERVAL_SECONDS
            if settings.ONEDRIVE_SYNC_ENABLED
            else None
        ),
        configuration_error=configuration_error,
        sources=entries,
    )
