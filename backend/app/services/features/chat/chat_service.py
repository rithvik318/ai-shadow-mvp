"""Stateless question answering over the user's own documents.

The orchestration layer, and only that: it asks `RetrievalService` for
context, renders the registered `rag_answer` prompt through `PromptBuilder`,
and sends the result to `LLMService`. It contains no retrieval logic, no
similarity arithmetic, no prompt text and no provider knowledge — each of those
belongs to the layer that already owns it, and this service composes them.

Stateless by design. No conversation history is kept or consulted.

**Not every message is a document question.** A greeting, or a question to the
twin about itself, is recognised by `intent.classify` before retrieval and
answered from the Digital Twin profile and memories through the
`twin_conversation` prompt — with no sources, because the profile is never a
source. Everything else goes through retrieval exactly as before, and an empty
result is still the fixed `NO_CONTEXT_ANSWER`. Without this split an empty
knowledge base answered "Hi" with "nothing in your documents".

**Sources come from retrieval, not from the model.** The passages returned in
`sources` are the chunks that were actually fetched and put in front of the
model, so a source cannot be fabricated — the model has no way to name a
document that was not retrieved. The cost of that guarantee is that every
retrieved chunk is listed, including any the model ignored while composing its
answer. Attribution at the sentence level would need the model to cite by
index and the `AnalysisEngine` to validate those indices; see
docs/DECISIONS.md.
"""

import enum
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import EmptyQueryError
from app.prompts.builder import PromptBuilder
from app.prompts.registry import PromptRegistry
from app.services.features.chat.context_service import build_context
from app.services.features.chat.intent import Intent, classify
from app.services.features.digital_twin import memory_service, profile_service
from app.services.features.digital_twin.persona_service import (
    KNOWLEDGE_HEADING,
    PersonaContext,
    build_persona_context,
)
from app.services.features.retrieval import retrieval_service
from app.services.features.retrieval.retrieval_service import UNSET
from app.services.llm.llm_service import llm_service

logger = logging.getLogger(__name__)

RAG_PROMPT_NAME = "rag_answer"
TWIN_PROMPT_NAME = "twin_conversation"

# Returned instead of calling the model when retrieval found nothing. Asking a
# model to answer with no context invites exactly the invention the prompt
# spends five rules forbidding, and charges for the privilege.
NO_CONTEXT_ANSWER = (
    "I could not find anything in your documents that answers that question."
)

# Returned instead of calling the model when asked about the twin and the twin
# has nothing to say about itself. With no profile, any self-description the
# model produced would be invented.
NO_PROFILE_ANSWER = (
    "This Digital Twin has no profile yet, so I can't describe who I represent. "
    "Add one on the Profile tab, or ask me about the documents in the knowledge "
    "base."
)


class AnswerBasis(enum.StrEnum):
    """What an answer was drawn from — so a client never has to infer it.

    `retrieved_chunks == 0` alone cannot tell "nothing matched" apart from "this
    was a greeting and nothing was searched", and the UI showed the no-match note
    under both.
    """

    DOCUMENTS = "documents"
    NO_MATCH = "no_match"
    CONVERSATION = "conversation"
    PROFILE = "profile"


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
    basis: AnswerBasis = AnswerBasis.DOCUMENTS


def _compose(persona: str, knowledge: str) -> str:
    """Put the persona above the passages, under headings that separate them.

    With no persona the knowledge text is passed through untouched — the same
    string this service built before the Digital Twin existed. The heading is
    only introduced when there is something above it to distinguish it from,
    because a lone `[KNOWLEDGE SOURCES]` label separates nothing.
    """

    if not persona:
        return knowledge

    return f"{persona}\n\n{KNOWLEDGE_HEADING}\n{knowledge}"


def _persona_for(db: Session, twin_user_id: uuid.UUID | None) -> PersonaContext:
    """This user's profile and memories, or an empty block when there is no
    user to load them for."""

    if twin_user_id is None:
        return PersonaContext(text="", memories=[])

    return build_persona_context(
        profile_service.find_profile(db, user_id=twin_user_id),
        memory_service.active_memories(db, user_id=twin_user_id),
        max_chars=settings.PERSONA_CONTEXT_MAX_CHARS,
    )


def _answer_conversationally(
    db: Session, message: str, intent: Intent, twin_user_id: uuid.UUID | None
) -> ChatAnswer:
    """Reply to a greeting or a question about the twin, from the persona alone.

    No retrieval and no sources: nothing here is evidence about the documents,
    so nothing may be presented as a citation.
    """

    persona = _persona_for(db, twin_user_id)
    basis = (
        AnswerBasis.PROFILE if intent is Intent.ABOUT_TWIN else AnswerBasis.CONVERSATION
    )

    if intent is Intent.ABOUT_TWIN and persona.is_empty:
        logger.info(
            "chat_answered_without_profile",
            extra={"twin_user_id": str(twin_user_id) if twin_user_id else None},
        )
        return ChatAnswer(
            answer=NO_PROFILE_ANSWER, sources=[], retrieved_chunks=0, basis=basis
        )

    messages = PromptBuilder.build(
        PromptRegistry.get(TWIN_PROMPT_NAME),
        persona=persona.text or "(no profile or memories recorded)",
        message=message,
    )

    started = time.perf_counter()
    answer = llm_service.complete(messages)
    llm_ms = (time.perf_counter() - started) * 1000

    logger.info(
        "chat_answered_conversationally",
        extra={
            "intent": intent.value,
            "twin_user_id": str(twin_user_id) if twin_user_id else None,
            "persona_chars": len(persona.text),
            "llm_duration_ms": round(llm_ms, 2),
            "answer_length": len(answer),
        },
    )

    return ChatAnswer(
        answer=answer.strip(), sources=[], retrieved_chunks=0, basis=basis
    )


def answer_question(
    db: Session,
    question: str,
    *,
    user_id: str = MVP_USER_ID,
    twin_user_id: uuid.UUID | None = None,
    top_k: int | None = None,
    similarity_threshold: float | None | object = UNSET,
) -> ChatAnswer:
    """Answer `question` from the shared documents, as `twin_user_id`.

    Two owners, and they are not the same thing. `user_id` owns the corpus and
    is shared — every person asking reads the same documents. `twin_user_id`
    owns the Digital Twin, and is one person's alone: their profile, their
    memories, nobody else's. Keeping them as separate arguments is what stops a
    future change to one silently widening the other.

    `twin_user_id=None` means no Digital Twin: no profile, no memories, the
    plain grounded answer this service gave before personas existed. It never
    means "use whichever profile is lying around" — there is no such profile to
    find, because every lookup below is scoped to an explicit id.

    A greeting or a question about the twin itself (`intent.classify`) is
    answered from the persona alone, without retrieval and without sources.

    Returns `NO_CONTEXT_ANSWER` with no sources when retrieval finds nothing,
    rather than raising: an empty knowledge base is a normal state, and the
    honest response to it is an answer, not an error.

    `RetrievalError` (blank question, invalid `top_k`), `EmbeddingError` and
    `LLMServiceError` propagate unchanged, so a provider outage is never
    reported as "I could not find anything".
    """

    if not question or not question.strip():
        raise EmptyQueryError("Question cannot be empty.")

    intent = classify(question)
    if intent is not Intent.DOCUMENTS:
        return _answer_conversationally(db, question.strip(), intent, twin_user_id)

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
        return ChatAnswer(
            answer=NO_CONTEXT_ANSWER,
            sources=[],
            retrieved_chunks=0,
            basis=AnswerBasis.NO_MATCH,
        )

    # Loaded only once retrieval has found something: with nothing to answer
    # from, the reply is fixed and neither the profile nor the memories change
    # it, so reading them would be two queries spent on a constant.
    persona = _persona_for(db, twin_user_id)

    # The persona block is taken *out of* the existing budget rather than added
    # to it, so a Digital Twin cannot grow the prompt past the bound that was
    # already there. With no profile and no memories the block is empty and the
    # knowledge context is built against exactly the budget it had before.
    knowledge_budget = max(settings.CHAT_CONTEXT_MAX_CHARS - len(persona.text), 0)
    context = build_context(chunks, max_chars=knowledge_budget)

    if len(context.chunks) < len(chunks):
        logger.info(
            "chat_context_truncated",
            extra={
                "user_id": user_id,
                "retrieved_chunks": len(chunks),
                "chunks_in_context": len(context.chunks),
            },
        )

    messages = PromptBuilder.build(
        PromptRegistry.get(RAG_PROMPT_NAME),
        context=_compose(persona.text, context.text),
        question=question.strip(),
    )

    started = time.perf_counter()
    answer = llm_service.complete(messages)
    llm_ms = (time.perf_counter() - started) * 1000

    logger.info(
        "chat_answered",
        extra={
            "user_id": user_id,
            "twin_user_id": str(twin_user_id) if twin_user_id else None,
            "retrieved_chunks": len(context.chunks),
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
            for chunk in context.chunks
        ],
        retrieved_chunks=len(context.chunks),
    )
