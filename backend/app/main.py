from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.document_routes import router as document_router
from app.config.settings import settings
from app.core.exceptions import (
    AnalysisValidationError,
    DocumentError,
    DocumentNotFoundError,
    DocumentParseError,
    DocumentTooLargeError,
    EmptyDocumentError,
    LLMServiceError,
    UnsupportedDocumentTypeError,
)
from app.prompts import register_default_prompts

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Document ingestion for the AI Shadow MVP: upload documents, extract "
        "and chunk their text, and store the chunks ready for embedding."
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


@app.exception_handler(LLMServiceError)
async def handle_llm_error(request: Request, exc: LLMServiceError) -> JSONResponse:
    """Map LLM failures to 502 without leaking provider internals."""

    detail = (
        "The language model returned a malformed response."
        if isinstance(exc, AnalysisValidationError)
        else "The language model provider is unavailable."
    )

    return JSONResponse(
        status_code=502,
        content={"detail": detail, "error": type(exc).__name__},
    )


app.include_router(document_router)


@app.get("/", tags=["health"], summary="Service information")
def root() -> dict[str, str]:
    return {"name": settings.APP_NAME, "status": "running"}


@app.get("/health", tags=["health"], summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "healthy"}
