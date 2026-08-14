from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.chat_routes import router as chat_router
from app.api.document_routes import router as document_router
from app.api.memory_routes import router as memory_router
from app.api.profile_routes import router as profile_router
from app.api.search_routes import router as search_router
from app.config.settings import settings
from app.core.exceptions import (
    AnalysisValidationError,
    DigitalTwinError,
    DocumentError,
    DocumentNotFoundError,
    DocumentParseError,
    DocumentTooLargeError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmptyDocumentError,
    LLMServiceError,
    MemoryNotFoundError,
    ProfileIncompleteError,
    ProfileNotFoundError,
    RetrievalError,
    UnsupportedDocumentTypeError,
)
from app.prompts import register_default_prompts

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "AI Shadow MVP: upload documents, index them, and ask questions "
        "answered only from their contents."
    ),
    version="0.1.0",
)

register_default_prompts()

# Domain errors are mapped to status codes in one place so that routes and
# services never construct HTTPException themselves.
_DOCUMENT_ERROR_STATUS: list[tuple[type[DocumentError], int]] = [
    (DocumentNotFoundError, 404),
    (DocumentTooLargeError, 413),
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


app.include_router(document_router)
app.include_router(search_router)
app.include_router(chat_router)
app.include_router(profile_router)
app.include_router(memory_router)


@app.get("/", tags=["health"], summary="Service information")
def root() -> dict[str, str]:
    return {"name": settings.APP_NAME, "status": "running"}


@app.get("/health", tags=["health"], summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "healthy"}
