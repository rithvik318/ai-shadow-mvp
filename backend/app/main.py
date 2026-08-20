import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.chat_routes import router as chat_router
from app.api.document_routes import router as document_router
from app.api.email_draft_routes import router as email_draft_router
from app.api.email_routes import router as email_router
from app.api.email_template_routes import router as email_template_router
from app.api.memory_routes import router as memory_router
from app.api.profile_routes import router as profile_router
from app.api.search_routes import router as search_router
from app.api.sync_routes import router as sync_router
from app.api.user_routes import router as user_router
from app.config.settings import settings
from app.core.exceptions import (
    AnalysisValidationError,
    BatchTooLargeError,
    DigitalTwinError,
    DocumentError,
    DocumentNotFoundError,
    DocumentParseError,
    DocumentTooLargeError,
    DuplicateEmailTemplateError,
    DuplicateUserError,
    EmailAttachmentTooLargeError,
    EmailDraftAlreadySentError,
    EmailDraftNotApprovedError,
    EmailDraftNotFoundError,
    EmailError,
    EmailProviderAuthError,
    EmailProviderNotConfiguredError,
    EmailSendError,
    EmailTemplateNotFoundError,
    EmailValidationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmptyAttachmentError,
    EmptyDocumentError,
    GraphError,
    IdentityError,
    LLMServiceError,
    MalformedIdentityError,
    MemoryNotFoundError,
    MissingIdentityError,
    ProfileIncompleteError,
    ProfileNotFoundError,
    RetrievalError,
    SyncAlreadyRunningError,
    SyncError,
    SyncNotConfiguredError,
    SyncSourceNotFoundError,
    TooManyAttachmentsError,
    UnsupportedDocumentTypeError,
    UserNotFoundError,
)
from app.prompts import register_default_prompts
from app.services.features.sync.scheduler import SyncScheduler

logger = logging.getLogger(__name__)


def _run_scheduled_sync() -> None:
    """One tick of the background sync, with its own database session.

    The request-scoped dependency is not available here — nothing is handling
    a request — so the session is opened and closed around the work.
    """

    from app.database.database import SessionLocal
    from app.services.features.sync import onedrive_sync_service

    db = SessionLocal()
    try:
        summaries = onedrive_sync_service.sync_all(db)
        logger.info(
            "scheduled_sync_completed",
            extra={"sources": len(summaries)},
        )
    finally:
        db.close()


scheduler = SyncScheduler(
    _run_scheduled_sync,
    interval_seconds=max(settings.ONEDRIVE_SYNC_INTERVAL_SECONDS, 1),
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the periodic sync, if this deployment has asked for one.

    Off unless `ONEDRIVE_SYNC_ENABLED` is set. The test suite constructs
    `TestClient(app)` without entering it as a context manager, so lifespan
    does not run there and no test can accidentally start a background job.
    """

    if settings.ONEDRIVE_SYNC_ENABLED:
        await scheduler.start()

    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "AI Shadow MVP: upload documents, index them, and ask questions "
        "answered only from their contents."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

register_default_prompts()

# Domain errors are mapped to status codes in one place so that routes and
# services never construct HTTPException themselves.
_DOCUMENT_ERROR_STATUS: list[tuple[type[DocumentError], int]] = [
    (DocumentNotFoundError, 404),
    (DocumentTooLargeError, 413),
    (BatchTooLargeError, 413),
    (UnsupportedDocumentTypeError, 415),
    (EmptyDocumentError, 422),
    (DocumentParseError, 422),
]


@app.exception_handler(DocumentError)
async def handle_document_error(request: Request, exc: DocumentError) -> JSONResponse:
    status_code = next(
        (
            code
            for error_type, code in _DOCUMENT_ERROR_STATUS
            if isinstance(exc, error_type)
        ),
        400,
    )

    return JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


_DIGITAL_TWIN_ERROR_STATUS: list[tuple[type[DigitalTwinError], int]] = [
    (ProfileNotFoundError, 404),
    (MemoryNotFoundError, 404),
    (ProfileIncompleteError, 422),
]


@app.exception_handler(DigitalTwinError)
async def handle_digital_twin_error(
    request: Request, exc: DigitalTwinError
) -> JSONResponse:
    """Map profile and memory errors the same way document errors are mapped.

    Kept separate from `DocumentError` rather than folded into it: a missing
    profile and a missing document are both 404, but nothing else about them is
    alike, and one hierarchy covering both would grow a branch per feature.
    """

    status_code = next(
        (
            code
            for error_type, code in _DIGITAL_TWIN_ERROR_STATUS
            if isinstance(exc, error_type)
        ),
        400,
    )

    return JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


_IDENTITY_ERROR_STATUS: list[tuple[type[IdentityError], int]] = [
    # 401 rather than 400: the request is well formed, it just does not say who
    # it is for. That is the status a client can act on once the header becomes
    # a real session.
    (MissingIdentityError, 401),
    (MalformedIdentityError, 422),
    (UserNotFoundError, 404),
    (DuplicateUserError, 409),
]


@app.exception_handler(IdentityError)
async def handle_identity_error(request: Request, exc: IdentityError) -> JSONResponse:
    """Map identity failures to statuses a caller can tell apart.

    Three ways to fail to name a user need three answers: say who you are, say
    it in the right shape, or create the user first. One status for all three
    would leave a caller guessing which.
    """

    status_code = next(
        (
            code
            for error_type, code in _IDENTITY_ERROR_STATUS
            if isinstance(exc, error_type)
        ),
        400,
    )

    return JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


@app.exception_handler(RetrievalError)
async def handle_retrieval_error(request: Request, exc: RetrievalError) -> JSONResponse:
    """Map search-request mistakes to 422.

    Kept apart from `LLMServiceError`: a blank question or an out-of-range
    `top_k` is the caller's to fix, while a provider outage is not, and giving
    both the same status would tell a client to retry when it should not — or
    not to, when it should.
    """

    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


@app.exception_handler(LLMServiceError)
async def handle_llm_error(request: Request, exc: LLMServiceError) -> JSONResponse:
    """Map LLM failures to 502 without leaking provider internals."""

    if isinstance(exc, EmbeddingDimensionError):
        # A configuration error, not a provider outage — say so, because
        # "provider unavailable" would send someone debugging the wrong thing.
        detail = "The embedding model does not match the configured vector width."
    elif isinstance(exc, EmbeddingError):
        detail = "The embedding provider is unavailable."
    elif isinstance(exc, AnalysisValidationError):
        detail = "The language model returned a malformed response."
    else:
        detail = "The language model provider is unavailable."

    return JSONResponse(
        status_code=502,
        content={"detail": detail, "error": type(exc).__name__},
    )


_EMAIL_ERROR_STATUS: list[tuple[type[EmailError], int]] = [
    (EmailTemplateNotFoundError, 404),
    (EmailDraftNotFoundError, 404),
    # 409 rather than 403: the draft exists and the caller owns it, the server
    # is simply not in a state where sending it is allowed. The remedy is an
    # action — approve it — not a permission.
    (EmailDraftNotApprovedError, 409),
    (EmailDraftAlreadySentError, 409),
    (DuplicateEmailTemplateError, 409),
    # Not a failure: the deployment has no mailbox. Same reasoning as
    # `SyncNotConfiguredError` — the request was well formed and the server
    # cannot satisfy it yet.
    (EmailProviderNotConfiguredError, 409),
    (TooManyAttachmentsError, 409),
    (EmailAttachmentTooLargeError, 413),
    (EmptyAttachmentError, 422),
    (EmailValidationError, 422),
    # The far end, not this one — the same 502 a Graph failure gets, so
    # "the mailbox is down" stays distinguishable from "this service is broken".
    (EmailProviderAuthError, 502),
    (EmailSendError, 502),
]


@app.exception_handler(EmailError)
async def handle_email_error(request: Request, exc: EmailError) -> JSONResponse:
    """Map email failures to statuses a client can act on.

    Ordered most specific first, because the subclasses overlap: an
    `EmailAttachmentTooLargeError` is also an `EmailAttachmentError`, and the
    first match wins.
    """

    status_code = next(
        (
            code
            for error_type, code in _EMAIL_ERROR_STATUS
            if isinstance(exc, error_type)
        ),
        400,
    )

    return JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


_SYNC_ERROR_STATUS: list[tuple[type[SyncError], int]] = [
    (SyncSourceNotFoundError, 404),
    # Already in flight. 409 rather than 429: nothing is rate-limiting the
    # caller, the resource is simply busy.
    (SyncAlreadyRunningError, 409),
    # Not a failure: the deployment has not been given anything to sync. 409
    # rather than 400 because the request was well-formed and the server is
    # simply not in a state to satisfy it.
    (SyncNotConfiguredError, 409),
    # The far end, not this one. 502 keeps "Graph is down" distinguishable
    # from "this service is broken" in any dashboard built on status codes.
    (GraphError, 502),
]


@app.exception_handler(SyncError)
async def handle_sync_error(request: Request, exc: SyncError) -> JSONResponse:
    status_code = next(
        (
            code
            for error_type, code in _SYNC_ERROR_STATUS
            if isinstance(exc, error_type)
        ),
        500,
    )

    return JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


app.include_router(document_router)
app.include_router(search_router)
app.include_router(chat_router)
app.include_router(user_router)
app.include_router(profile_router)
app.include_router(memory_router)
app.include_router(sync_router)
app.include_router(email_router)
app.include_router(email_draft_router)
app.include_router(email_template_router)


@app.get("/", tags=["health"], summary="Service information")
def root() -> dict[str, str]:
    return {"name": settings.APP_NAME, "status": "running"}


@app.get("/health", tags=["health"], summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "healthy"}
