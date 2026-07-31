from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

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
        422: {"model": ErrorResponse, "description": "Blank question or invalid top_k"},
        502: {
            "model": ErrorResponse,
            "description": "The embedding or language model provider failed",
        },
    },
)
def ask(
    request: ChatRequest,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Answer a question using only the caller's indexed documents.

    Stateless: no conversation history is kept or consulted.

    An empty or irrelevant knowledge base is answered, not raised — the reply
    says nothing was found and `retrieved_chunks` is 0. Provider failures
    return 502 so they are never mistaken for that case.
    """

    answer = chat_service.answer_question(db, request.question, top_k=request.top_k)

    return ChatResponse(
        answer=answer.answer,
        sources=[
            ChatSourceResponse.model_validate(source) for source in answer.sources
        ],
        retrieved_chunks=answer.retrieved_chunks,
    )
