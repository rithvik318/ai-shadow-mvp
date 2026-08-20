"""The Email Agent's writing half: turning an instruction into a draft.

Orchestration only, exactly like `chat_service`. It asks the existing services
for their pieces and composes them:

- `profile_service` + `memory_service` + `persona_service` — who is writing
- `retrieval_service` + `context_service` — what the company actually knows
- `PromptRegistry` + `PromptBuilder` — what the model is told
- `analysis_engine` — the call, and the guarantee the reply is the right shape

There is no second persona, no second retriever, no second prompt mechanism and
no direct provider call. The one thing that is new here is the **operation**: a
small set of named transformations, so "make this shorter" is data rather than
an endpoint.

Two properties are load-bearing and are asserted in tests rather than hoped for:

**Different people get different drafts.** The persona block is built from the
`twin_user_id` passed in, and from nothing else. Two users with the same
instruction and the same knowledge base see different prompts.

**Nothing is invented about the company.** Retrieval is opt-in. When it is off,
or when it returns nothing, the prompt carries an explicit statement that no
company knowledge was retrieved, and the system prompt forbids company claims
in that case. `knowledge_used` in the result says which happened, so the API —
and the person reading the draft — can tell.
"""

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.constants import MVP_USER_ID
from app.core.exceptions import EmailValidationError
from app.services.engines.analysis.analysis_engine import analysis_engine
from app.services.features.chat.context_service import build_context
from app.services.features.digital_twin import memory_service, profile_service
from app.services.features.digital_twin.persona_service import (
    PersonaContext,
    build_persona_context,
)
from app.services.features.retrieval import retrieval_service
from app.services.features.retrieval.retrieval_service import RetrievedChunk

logger = logging.getLogger(__name__)

COMPOSE_PROMPT_NAME = "email_compose"
TRANSFORM_PROMPT_NAME = "email_transform"
SUBJECT_PROMPT_NAME = "email_subject"

# Rendered into the prompt where retrieved passages would go. Explicit text
# rather than an empty section, because an empty section reads as "nothing to
# say about this" and this has to read as "you have been told nothing, so claim
# nothing" — the difference between a cautious email and an invented one.
NO_KNOWLEDGE = (
    "No company knowledge was retrieved for this request. Do not state any "
    "fact about the sender's company, its services, its clients or its "
    "results. Write the email without them."
)

NO_PERSONA = (
    "[DIGITAL TWIN PROFILE]\nNo profile has been set up for this user. Write "
    "in a neutral professional voice and do not invent a role, a company or a "
    "signature."
)

NO_SOURCE_EMAIL = "None. This is not a reply."


class EmailOperation(StrEnum):
    """What to do, as data rather than as an endpoint each.

    Grouped by which prompt runs, which is also the only grouping that matters:
    `GENERATE` and `REPLY` produce an email from an instruction, `SUBJECT`
    produces a subject line, and everything else revises an email that already
    exists.
    """

    GENERATE = "generate"
    REPLY = "reply"
    REWRITE = "rewrite"
    IMPROVE = "improve"
    SHORTEN = "shorten"
    EXPAND = "expand"
    CHANGE_TONE = "change_tone"
    PROFESSIONAL = "professional"
    CONCISE = "concise"
    SUBJECT = "subject"


GENERATING_OPERATIONS = frozenset({EmailOperation.GENERATE, EmailOperation.REPLY})
TRANSFORMING_OPERATIONS = frozenset(
    {
        EmailOperation.REWRITE,
        EmailOperation.IMPROVE,
        EmailOperation.SHORTEN,
        EmailOperation.EXPAND,
        EmailOperation.CHANGE_TONE,
        EmailOperation.PROFESSIONAL,
        EmailOperation.CONCISE,
    }
)

# What each revision means, in one sentence, in the prompt's own voice. Kept
# here rather than in the template because the template is one prompt serving
# seven operations — putting seven branches in prompt text would be a prompt
# that has to be re-read in full to change one of them.
_TRANSFORM_INSTRUCTIONS: dict[EmailOperation, str] = {
    EmailOperation.REWRITE: (
        "Rewrite this email. Keep every fact and every request it makes, and "
        "change how it is expressed."
    ),
    EmailOperation.IMPROVE: (
        "Improve the clarity of this email. Put the point first, remove "
        "hedging and repetition, and make each sentence say one thing."
    ),
    EmailOperation.SHORTEN: (
        "Shorten this email substantially. Keep every fact, request and "
        "condition; remove pleasantries, restatement and throat-clearing."
    ),
    EmailOperation.EXPAND: (
        "Expand this email with more context and detail — but only detail "
        "already present or implied in it. Do not introduce new facts."
    ),
    EmailOperation.CHANGE_TONE: (
        "Rewrite this email in the tone described under [REQUESTED TONE], "
        "without changing what it says or asks for."
    ),
    EmailOperation.PROFESSIONAL: (
        "Make this email more professional: measured, specific, free of slang "
        "and of exclamation marks, while staying warm rather than stiff."
    ),
    EmailOperation.CONCISE: (
        "Make this email more concise. Same content, fewer words, no lost "
        "conditions or qualifications."
    ),
}


class GeneratedEmail(BaseModel):
    """The shape the model must return for a compose or transform.

    Validated by the `AnalysisEngine`, so a model that answers with prose or
    with an apology produces `AnalysisValidationError` — a 502 — rather than an
    email body containing an apology.
    """

    subject: str = Field(default="")
    body: str


class GeneratedSubject(BaseModel):
    """The shape the model must return for a subject line."""

    subject: str


@dataclass(frozen=True)
class ComposedEmail:
    """A generated draft, and an honest account of what produced it."""

    subject: str
    body: str
    operation: EmailOperation
    # The passages the model was actually shown. Built from retrieval, never
    # from the model's output — so, exactly as in chat, a source cannot be
    # fabricated.
    sources: list[RetrievedChunk] = field(default_factory=list)
    knowledge_used: bool = False
    persona_used: bool = False


def _persona(db: Session, twin_user_id: uuid.UUID | None) -> PersonaContext:
    """This user's profile and memories, or nothing.

    `twin_user_id=None` is not "whichever profile is lying around" — there is
    no such lookup here. Every query below names an id.
    """

    if twin_user_id is None:
        return PersonaContext(text="", memories=[])

    return build_persona_context(
        profile_service.find_profile(db, user_id=twin_user_id),
        memory_service.active_memories(db, user_id=twin_user_id),
        max_chars=settings.PERSONA_CONTEXT_MAX_CHARS,
    )


def _knowledge(
    db: Session, query: str, *, use_knowledge_base: bool, user_id: str
) -> tuple[str, list[RetrievedChunk]]:
    """Retrieved company passages, rendered, or the explicit no-knowledge text.

    Retrieval failures are not swallowed. An embedding provider outage must
    surface as an outage, because the alternative — quietly writing an email
    with no company knowledge — produces a plausible message that is missing
    exactly the substance it was asked for.
    """

    if not use_knowledge_base or not query.strip():
        return NO_KNOWLEDGE, []

    chunks = retrieval_service.search(
        db, query, user_id=user_id, top_k=settings.EMAIL_RETRIEVAL_TOP_K
    )

    if not chunks:
        logger.info("email_composed_without_knowledge", extra={"user_id": user_id})
        return NO_KNOWLEDGE, []

    context = build_context(chunks, max_chars=settings.EMAIL_CONTEXT_MAX_CHARS)

    if not context.chunks:
        return NO_KNOWLEDGE, []

    return context.text, context.chunks


def _describe_message(
    *,
    subject: str | None,
    body: str | None,
    sender: str | None = None,
    received_at: str | None = None,
) -> str:
    """Render a received message for a prompt, labelled so it cannot be
    mistaken for the draft being written."""

    lines: list[str] = []

    if sender:
        lines.append(f"From: {sender}")

    if received_at:
        lines.append(f"Received: {received_at}")

    lines.append(f"Subject: {subject or '(no subject)'}")
    lines.append("")
    lines.append(body or "(no body)")

    return "\n".join(lines)


def compose(
    db: Session,
    *,
    operation: EmailOperation,
    instruction: str | None = None,
    subject: str = "",
    body: str = "",
    tone: str | None = None,
    recipients: list[str] | None = None,
    source_subject: str | None = None,
    source_body: str | None = None,
    source_sender: str | None = None,
    use_knowledge_base: bool = False,
    twin_user_id: uuid.UUID | None = None,
    corpus_user_id: str = MVP_USER_ID,
) -> ComposedEmail:
    """Run one email operation and return the text it produced.

    Two owners, as everywhere else in this system. `twin_user_id` owns the
    Digital Twin — one person's profile and memories. `corpus_user_id` owns the
    shared company knowledge base, and is the same for everyone. They are
    separate arguments so that a future change to one cannot widen the other.

    The result is *text*. Nothing is saved and nothing is sent: the caller
    decides whether this becomes a draft, and a person decides whether that
    draft is ever approved.
    """

    if operation in GENERATING_OPERATIONS and not (instruction or "").strip():
        raise EmailValidationError(
            "Say what the email should do — for example, 'follow up on "
            "yesterday's meeting and ask for a decision by Friday'."
        )

    if operation in TRANSFORMING_OPERATIONS and not (body or "").strip():
        raise EmailValidationError("There is no draft to revise yet.")

    if operation is EmailOperation.SUBJECT and not (body or "").strip():
        raise EmailValidationError("Write the message before generating a subject.")

    if operation is EmailOperation.CHANGE_TONE and not (tone or "").strip():
        raise EmailValidationError("Say which tone you want.")

    persona = _persona(db, twin_user_id)
    persona_text = persona.text or NO_PERSONA

    if operation is EmailOperation.SUBJECT:
        generated_subject = analysis_engine.run(
            SUBJECT_PROMPT_NAME,
            GeneratedSubject,
            persona=persona_text,
            body=body,
            instruction=(instruction or "").strip()
            or "Write a subject line for this message.",
        )

        return ComposedEmail(
            subject=generated_subject.subject.strip(),
            # Unchanged: this operation writes a subject and touches nothing
            # else, and returning a re-generated body would quietly discard an
            # edit the person had made.
            body=body,
            operation=operation,
            persona_used=not persona.is_empty,
        )

    if operation in TRANSFORMING_OPERATIONS:
        # Retrieval is keyed on the draft itself. `EXPAND` is the operation
        # that most often wants company material, and the draft is the only
        # description of the subject matter available at this point.
        knowledge_text, chunks = _knowledge(
            db,
            f"{subject}\n{body}",
            use_knowledge_base=use_knowledge_base,
            user_id=corpus_user_id,
        )

        transform_instruction = _TRANSFORM_INSTRUCTIONS[operation]

        if (instruction or "").strip():
            # A caller-supplied instruction refines the operation rather than
            # replacing it — "shorten, and drop the pricing paragraph".
            transform_instruction = (
                f"{transform_instruction}\n\nAlso: {instruction.strip()}"
            )

        if operation is EmailOperation.CHANGE_TONE:
            transform_instruction = (
                f"{transform_instruction}\n\n[REQUESTED TONE]\n{tone}"
            )

        generated = analysis_engine.run(
            TRANSFORM_PROMPT_NAME,
            GeneratedEmail,
            persona=persona_text,
            knowledge=knowledge_text,
            subject=subject,
            body=body,
            instruction=transform_instruction,
        )

        return ComposedEmail(
            subject=(generated.subject.strip() or subject),
            body=generated.body.strip(),
            operation=operation,
            sources=chunks,
            knowledge_used=bool(chunks),
            persona_used=not persona.is_empty,
        )

    # GENERATE and REPLY.
    source_email = NO_SOURCE_EMAIL

    if operation is EmailOperation.REPLY:
        if not (source_body or "").strip() and not (source_subject or "").strip():
            raise EmailValidationError("A reply needs the message it is replying to.")

        source_email = _describe_message(
            subject=source_subject, body=source_body, sender=source_sender
        )

    # Retrieval is keyed on the instruction plus, for a reply, the message
    # being answered — which is usually where the subject matter actually is.
    query = (instruction or "").strip()
    if operation is EmailOperation.REPLY:
        query = f"{query}\n{source_subject or ''}\n{source_body or ''}"

    knowledge_text, chunks = _knowledge(
        db, query, use_knowledge_base=use_knowledge_base, user_id=corpus_user_id
    )

    generated = analysis_engine.run(
        COMPOSE_PROMPT_NAME,
        GeneratedEmail,
        persona=persona_text,
        knowledge=knowledge_text,
        source_email=source_email,
        recipients=", ".join(recipients or []) or "Not yet decided.",
        tone=(tone or "").strip() or "Follow the profile's communication style.",
        instruction=(instruction or "").strip(),
    )

    logger.info(
        "email_generated",
        extra={
            "operation": operation.value,
            "twin_user_id": str(twin_user_id) if twin_user_id else None,
            "knowledge_used": bool(chunks),
            "retrieved_chunks": len(chunks),
            "persona_used": not persona.is_empty,
        },
    )

    return ComposedEmail(
        subject=generated.subject.strip(),
        body=generated.body.strip(),
        operation=operation,
        sources=chunks,
        knowledge_used=bool(chunks),
        persona_used=not persona.is_empty,
    )
