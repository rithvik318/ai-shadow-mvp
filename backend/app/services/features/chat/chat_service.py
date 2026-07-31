"""Stateless question answering over the user's own documents.

The orchestration layer, and only that: it asks `RetrievalService` for
context, renders the registered `rag_answer` prompt through `PromptBuilder`,
and sends the result to `LLMService`. It contains no retrieval logic, no
similarity arithmetic, no prompt text and no provider knowledge — each of those
belongs to the layer that already owns it, and this service composes them.

Stateless by design. No conversation history is kept or consulted; every
question is answered from the documents alone.

**Sources come from retrieval, not from the model.** The passages returned in
`sources` are the chunks that were actually fetched and put in front of the
model, so a source cannot be fabricated — the model has no way to name a
document that was not retrieved. The cost of that guarantee is that every
retrieved chunk is listed, including any the model ignored while composing its
answer. Attribution at the sentence level would need the model to cite by
index and the `AnalysisEngine` to validate those indices; see
docs/DECISIONS.md.
"""

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import EmptyQueryError
from app.prompts.builder import PromptBuilder
from app.prompts.registry import PromptRegistry
from app.services.features.retrieval import retrieval_service
from app.services.features.retrieval.retrieval_service import UNSET, RetrievedChunk
from app.services.llm.llm_service import llm_service

logger = logging.getLogger(__name__)

RAG_PROMPT_NAME = "rag_answer"

# Returned instead of calling the model when retrieval found nothing. Asking a
# model to answer with no context invites exactly the invention the prompt
# spends five rules forbidding, and charges for the privilege.
NO_CONTEXT_ANSWER = (
    "I could not find anything in your documents that answers that question."
)


@dataclass(frozen=True)
class ChatSource:
    """One retrieved passage, described well enough to look it up."""

    document_id: uuid.UUID
    filename: str
    similarity: float
    chunk_id: uuid.UUID
    page_number: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class ChatAnswer:
    """A grounded answer and the passages it was allowed to draw on."""

    answer: str
    sources: list[ChatSource]
    retrieved_chunks: int


def _format_context(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks as numbered, attributed passages.

    Each passage is labelled with its document and, where the format has one,
    its page — so the model can distinguish two passages that say similar
    things, and can qualify an answer that holds in one document but not
    another.
    """

    passages: list[str] = []

    for position, chunk in enumerate(chunks, start=1):
        location = chunk.filename

        if chunk.page_number is not None:
            location = f"{location}, page {chunk.page_number}"
        elif chunk.section_title:
            location = f"{location}, section {chunk.section_title!r}"

        passages.append(f"[{position}] {location}\n{chunk.content}")

    return "\n\n".join(passages)


def answer_question(
    db: Session,
    question: str,
    *,
    user_id: str = MVP_USER_ID,
    top_k: int | None = None,
    similarity_threshold: float | None | object = UNSET,
) -> ChatAnswer:
    """Answer `question` from the user's indexed documents.

    Returns `NO_CONTEXT_ANSWER` with no sources when retrieval finds nothing,
    rather than raising: an empty knowledge base is a normal state, and the
    honest response to it is an answer, not an error.

    `RetrievalError` (blank question, invalid `top_k`), `EmbeddingError` and
    `LLMServiceError` propagate unchanged, so a provider outage is never
    reported as "I could not find anything".
    """

    if not question or not question.strip():
        raise EmptyQueryError("Question cannot be empty.")

    chunks = retrieval_service.search(
        db,
        question,
        user_id=user_id,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )

    if not chunks:
        logger.info(
            "chat_answered_without_context",
            extra={"user_id": user_id, "retrieved_chunks": 0},
        )
        return ChatAnswer(answer=NO_CONTEXT_ANSWER, sources=[], retrieved_chunks=0)

    messages = PromptBuilder.build(
        PromptRegistry.get(RAG_PROMPT_NAME),
        context=_format_context(chunks),
        question=question.strip(),
    )

    started = time.perf_counter()
    answer = llm_service.complete(messages)
    llm_ms = (time.perf_counter() - started) * 1000

    logger.info(
        "chat_answered",
        extra={
            "user_id": user_id,
            "retrieved_chunks": len(chunks),
            "llm_duration_ms": round(llm_ms, 2),
            "answer_length": len(answer),
        },
    )

    return ChatAnswer(
        answer=answer.strip(),
        sources=[
            ChatSource(
                document_id=chunk.document_id,
                filename=chunk.filename,
                similarity=chunk.similarity,
                chunk_id=chunk.chunk_id,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
            )
            for chunk in chunks
        ],
        retrieved_chunks=len(chunks),
    )
