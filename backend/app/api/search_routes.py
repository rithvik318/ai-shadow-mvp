from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.document_schema import ErrorResponse
from app.schemas.search_schema import (
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from app.services.features.retrieval import retrieval_service

router = APIRouter(prefix="/search", tags=["search"])


@router.post(
    "",
    response_model=SearchResponse,
    summary="Search your documents without answering from them",
    responses={
        422: {"model": ErrorResponse, "description": "Blank query or invalid top_k"},
        502: {
            "model": ErrorResponse,
            "description": "The embedding provider failed",
        },
    },
)
def search(
    request: SearchRequest,
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Return the passages retrieval would hand to the model, and stop there.

    No prompt is rendered and no completion is requested, so what comes back is
    what `POST /chat` would have been shown for the same query — without the
    latency, the cost, or the model's opinion about it. That is what makes this
    the endpoint to tune `RETRIEVAL_SIMILARITY_THRESHOLD` against.

    Calls `retrieval_service.search` directly rather than through a service of
    its own: retrieval *is* the feature service here, and a layer between it
    and this route would have one caller and nothing of its own to say
    (CLAUDE.md §3, "no premature abstraction"). Chat has such a service because
    it composes retrieval, context assembly, a prompt and a provider; this
    composes nothing.

    Nothing found is `200` with an empty `results`, never `404` — an empty or
    irrelevant knowledge base is a normal state, and a client tells it apart
    from a full one by `retrieved_count`.
    """

    chunks = retrieval_service.search(db, request.question, top_k=request.top_k)

    return SearchResponse(
        # The trimmed query, because that is the text that was embedded and so
        # the text the scores below actually belong to.
        query=request.question.strip(),
        results=[
            SearchResultResponse(
                document=chunk.filename,
                section=chunk.section_title,
                page=chunk.page_number,
                similarity=chunk.similarity,
                content=chunk.content,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
            )
            for chunk in chunks
        ],
        retrieved_count=len(chunks),
    )
