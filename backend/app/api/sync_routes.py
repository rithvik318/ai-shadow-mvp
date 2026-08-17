from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.session import get_db
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
from app.services.features.sync.source_config import load_sources

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

    try:
        configured = bool(load_sources())
    except Exception:  # noqa: BLE001 - a malformed config is "not configured"
        configured = False

    return SyncStatusResponse(
        configured=configured,
        scheduled=settings.ONEDRIVE_SYNC_ENABLED,
        interval_seconds=(
            settings.ONEDRIVE_SYNC_INTERVAL_SECONDS
            if settings.ONEDRIVE_SYNC_ENABLED
            else None
        ),
        sources=[
            SyncStateResponse(
                source_key=state.source_key,
                label=state.label,
                drive_id=state.drive_id,
                item_id=state.item_id,
                status=state.status,
                has_delta_token=bool(state.delta_link),
                error_message=state.error_message,
                last_attempted_at=state.last_attempted_at,
                last_succeeded_at=state.last_succeeded_at,
                last_duration_ms=state.last_duration_ms,
                last_indexed=state.last_indexed,
                last_replaced=state.last_replaced,
                last_unchanged=state.last_unchanged,
                last_deleted=state.last_deleted,
                last_unsupported=state.last_unsupported,
                last_failed=state.last_failed,
            )
            for state in onedrive_sync_service.list_states(db)
        ],
    )
