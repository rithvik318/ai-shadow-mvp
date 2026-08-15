from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.database.session import get_db
from app.schemas.chat_schema import ChatRequest, ChatResponse, ChatSourceResponse
from app.schemas.document_schema import ErrorResponse
from app.services.features.chat import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Ask a question about your documents",
    responses={
        401: {"model": ErrorResponse, "description": "No X-User-ID header"},
        404: {"model": ErrorResponse, "description": "Unknown user"},
        422: {"model": ErrorResponse, "description": "Blank question or invalid top_k"},
        502: {
            "model": ErrorResponse,
            "description": "The embedding or language model provider failed",
        },
    },
)
def ask(
    request: ChatRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Answer a question from the shared documents, as the current user.

    The company knowledge base is shared: two people asking the same question
    search the same documents and cite the same sources. What differs is the
    Digital Twin — the profile and memories of the user named by `X-User-ID`
    shape the tone, the emphasis and which options are raised, and nobody
    else's ever appear.

    The request body is unchanged, and carries no identity: `X-User-ID` is the
    only thing that decides whose twin is loaded, which is what lets it be
    replaced by a real session without touching this contract.

    Stateless: no conversation history is kept or consulted.

    An empty or irrelevant knowledge base is answered, not raised — the reply
    says nothing was found and `retrieved_chunks` is 0. Provider failures
    return 502 so they are never mistaken for that case.
    """

    answer = chat_service.answer_question(
        db, request.question, twin_user_id=user.id, top_k=request.top_k
    )

    return ChatResponse(
        answer=answer.answer,
        sources=[
            ChatSourceResponse(
                document=source.filename,
                section=source.section_title,
                page=source.page_number,
                similarity=source.similarity,
                document_id=source.document_id,
                chunk_id=source.chunk_id,
            )
            for source in answer.sources
        ],
        retrieved_chunks=answer.retrieved_chunks,
    )
