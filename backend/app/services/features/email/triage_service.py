"""The Email Agent's reading half: classify, summarise, and spot follow-ups.

Every judgement here goes through the `AnalysisEngine` with a Pydantic model,
so the model returns a validated object or the call fails. That matters more
for triage than for writing: a malformed reply to "write me an email" is
visible to whoever reads the draft, while a malformed reply to "is this urgent"
would become a category in a list nobody re-reads.

Assessments are **stored**, keyed by the provider and its own message id, and
re-assessing a message updates the row rather than adding a second opinion.
Storing them is not caching for its own sake: an assessment costs a model call,
and recomputing every row each time an inbox is opened is a bill and a delay
for an answer that has not changed.

Nothing here fabricates a message. `assess_message` is given one — by a
provider, or by a caller who has one — and there is no code path that invents
mail. A deployment with no mailbox has no assessments, and its inbox view says
so rather than showing examples.

Follow-up is a **recommendation**, never an action. This module can conclude
that somebody should be followed up and can draft the follow-up when asked. It
cannot schedule anything, cannot send anything, and marks nothing handled on a
person's behalf.
"""

import logging
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import EmailValidationError
from app.models.email import (
    EmailAssessment,
    EmailCategory,
    EmailPriority,
)
from app.services.email.provider.base import EmailMessage
from app.services.engines.analysis.analysis_engine import analysis_engine
from app.services.features.digital_twin import memory_service, profile_service
from app.services.features.digital_twin.persona_service import (
    PersonaContext,
    build_persona_context,
)

logger = logging.getLogger(__name__)

TRIAGE_PROMPT_NAME = "email_triage"
THREAD_SUMMARY_PROMPT_NAME = "email_thread_summary"

NO_PERSONA = (
    "[DIGITAL TWIN PROFILE]\nNo profile has been set up for this user. Judge "
    "urgency on the message alone."
)

# A body long enough to blow any prompt budget is truncated rather than
# refused: the opening of an email is where its purpose is, and triaging the
# first few thousand characters of a long thread is far better than declining
# to triage it.
MAX_MESSAGE_CHARS = 8000


def _coerce_datetime(value: str | None) -> datetime | None:
    """Parse the model's ISO 8601, or give up quietly.

    Giving up is right here. A due date is the one field where a wrong value is
    worse than no value, so anything that does not parse cleanly becomes None
    rather than something approximate.
    """

    if not value or not str(value).strip():
        return None

    text = str(value).strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class TriageVerdict(BaseModel):
    """What the model must return for one message."""

    category: EmailCategory
    priority: EmailPriority
    summary: str
    suggested_action: str | None = None
    action_items: list[str] = Field(default_factory=list)
    follow_up_recommended: bool = False
    follow_up_reason: str | None = None
    follow_up_due_at: str | None = None

    @field_validator("action_items", mode="before")
    @classmethod
    def tolerate_a_single_string(cls, value: object) -> object:
        """Models occasionally answer with one string where a list was asked
        for. Accepting that is cheaper than a 502 and loses nothing."""

        if isinstance(value, str):
            return [value] if value.strip() else []

        return value or []


class ThreadSummary(BaseModel):
    """What the model must return for a conversation."""

    summary: str
    action_items: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    follow_up_recommended: bool = False
    follow_up_reason: str | None = None

    @field_validator("action_items", mode="before")
    @classmethod
    def tolerate_a_single_string(cls, value: object) -> object:
        if isinstance(value, str):
            return [value] if value.strip() else []

        return value or []


def _persona(db: Session, twin_user_id: uuid.UUID) -> PersonaContext:
    return build_persona_context(
        profile_service.find_profile(db, user_id=twin_user_id),
        memory_service.active_memories(db, user_id=twin_user_id),
        max_chars=settings.PERSONA_CONTEXT_MAX_CHARS,
    )


def render_message(message: EmailMessage) -> str:
    """One message as the model sees it.

    Public and pure, so a test can assert exactly what a given message becomes
    without a model, a provider or a database in the way.
    """

    lines = [
        f"From: {message.sender}" if message.sender else "From: (unknown)",
        "To: " + (", ".join(str(item) for item in message.to_recipients) or "(none)"),
    ]

    if message.cc_recipients:
        lines.append("Cc: " + ", ".join(str(item) for item in message.cc_recipients))

    if message.received_at:
        lines.append(f"Received: {message.received_at.isoformat()}")

    if message.attachments:
        lines.append(
            "Attachments: " + ", ".join(item.filename for item in message.attachments)
        )

    lines.append(f"Subject: {message.subject or '(no subject)'}")
    lines.append("")
    lines.append((message.text or "(no body)")[:MAX_MESSAGE_CHARS])

    return "\n".join(lines)


def render_thread(messages: list[EmailMessage]) -> str:
    """A conversation, oldest first, with each message delimited.

    Delimited by index rather than run together, so the model can attribute a
    position to the message it came from — the difference between a summary and
    an averaged impression of one.
    """

    blocks = [
        f"--- MESSAGE {position} ---\n{render_message(message)}"
        for position, message in enumerate(messages, start=1)
    ]

    return "\n\n".join(blocks)


def assess_message(
    db: Session,
    message: EmailMessage,
    *,
    provider: str,
    user_id: uuid.UUID,
    persist: bool = True,
) -> EmailAssessment:
    """Classify and summarise one real message, and store the verdict.

    `message` comes from a provider or from a caller holding one. This function
    never goes looking for mail, which is what keeps an unconnected deployment
    honestly empty.

    Re-assessing updates the existing row. The unique constraint on
    (user, provider, message) is what makes that a guarantee rather than a
    convention — two assessments of one message would give the inbox two
    answers and no way to choose.
    """

    if not message.message_id:
        raise EmailValidationError("A message needs an id before it can be triaged.")

    if not (message.text or "").strip() and not (message.subject or "").strip():
        raise EmailValidationError("That message has no subject and no body.")

    persona = _persona(db, user_id)

    verdict = analysis_engine.run(
        TRIAGE_PROMPT_NAME,
        TriageVerdict,
        persona=persona.text or NO_PERSONA,
        message=render_message(message),
    )

    values = {
        "provider_thread_id": message.thread_id,
        "subject": message.subject or None,
        # Structure preserved, not flattened. `sender_address` is what a
        # follow-up is addressed to; `sender_name` is only ever displayed.
        "sender_name": message.sender.name if message.sender else None,
        "sender_address": message.sender.address if message.sender else None,
        "received_at": message.received_at,
        "category": verdict.category,
        "priority": verdict.priority,
        "summary": verdict.summary.strip(),
        "suggested_action": (
            verdict.suggested_action.strip() if verdict.suggested_action else None
        ),
        "action_items": [item.strip() for item in verdict.action_items if item.strip()],
        "follow_up_recommended": verdict.follow_up_recommended,
        "follow_up_reason": (
            verdict.follow_up_reason.strip() if verdict.follow_up_reason else None
        ),
        "follow_up_due_at": _coerce_datetime(verdict.follow_up_due_at),
        "assessed_at": datetime.now(UTC),
    }

    identity = {
        "user_id": user_id,
        "provider": provider,
        "provider_message_id": message.message_id,
    }

    if not persist:
        # The "assess this without keeping it" path: a message somebody pasted
        # in, which is in no mailbox and must not appear in a follow-up list.
        # Built detached and never added to the session, so there is no
        # mutate-then-rollback dance and no chance of a stray flush writing it.
        #
        # `id` and `handled` are set explicitly. Their column `default=` values
        # are applied by the INSERT, and this row is never inserted — so
        # without this the attributes are None and the response model, which
        # requires both, rejects a perfectly good assessment. Giving it a real
        # id is not invented data: it identifies this result inside this
        # response, exactly as the persisted path's id does. It addresses no
        # stored row, which is correct — an unpersisted assessment is not one.
        identity["id"] = uuid.uuid4()
        values["handled"] = False

        logger.info(
            "email_assessed",
            extra={
                "user_id": str(user_id),
                "provider": provider,
                "category": verdict.category.value,
                "priority": verdict.priority.value,
                "follow_up": verdict.follow_up_recommended,
                "persisted": False,
            },
        )

        return EmailAssessment(**identity, **values)

    assessment = find_assessment(
        db, provider=provider, message_id=message.message_id, user_id=user_id
    )

    if assessment is None:
        assessment = EmailAssessment(**identity)
        db.add(assessment)

    for attribute, value in values.items():
        setattr(assessment, attribute, value)

    db.commit()
    db.refresh(assessment)

    logger.info(
        "email_assessed",
        extra={
            "user_id": str(user_id),
            "provider": provider,
            "category": assessment.category.value,
            "priority": assessment.priority.value,
            "follow_up": assessment.follow_up_recommended,
            "persisted": True,
        },
    )

    return assessment


def summarize_thread(
    db: Session,
    messages: list[EmailMessage],
    *,
    user_id: uuid.UUID,
) -> ThreadSummary:
    """Summarise a conversation for this user. Nothing is stored.

    Not persisted, unlike an assessment: a thread grows, and a stored summary
    of it is wrong the moment somebody replies. It is cheap enough to recompute
    when asked and always describes the thread as it is now.
    """

    if not messages:
        raise EmailValidationError("There are no messages to summarise.")

    persona = _persona(db, user_id)

    return analysis_engine.run(
        THREAD_SUMMARY_PROMPT_NAME,
        ThreadSummary,
        persona=persona.text or NO_PERSONA,
        thread=render_thread(messages),
    )


# --- stored assessments and follow-ups -----------------------------------


def find_assessment(
    db: Session, *, provider: str, message_id: str, user_id: uuid.UUID
) -> EmailAssessment | None:
    """This user's assessment of that message, if it has been triaged."""

    return db.execute(
        select(EmailAssessment).where(
            EmailAssessment.user_id == user_id,
            EmailAssessment.provider == provider,
            EmailAssessment.provider_message_id == message_id,
        )
    ).scalar_one_or_none()


def list_assessments(
    db: Session,
    *,
    category: EmailCategory | None = None,
    user_id: uuid.UUID,
) -> list[EmailAssessment]:
    """Every stored assessment for this user, most recent message first."""

    predicates = [EmailAssessment.user_id == user_id]

    if category is not None:
        predicates.append(EmailAssessment.category == category)

    return list(
        db.execute(
            select(EmailAssessment)
            .where(*predicates)
            .order_by(
                EmailAssessment.received_at.desc(),
                EmailAssessment.assessed_at.desc(),
                EmailAssessment.id,
            )
        )
        .scalars()
        .all()
    )


def list_follow_ups(
    db: Session, *, include_handled: bool = False, user_id: uuid.UUID
) -> list[EmailAssessment]:
    """Messages triage thinks need following up, soonest due first.

    Recommendations only. Nothing in this system acts on them, and a person
    marks each one handled.

    Rows with no due date sort last rather than first: a dated commitment is
    the one that can be missed, and an undated "should probably circle back"
    should not push it down the page.
    """

    predicates = [
        EmailAssessment.user_id == user_id,
        EmailAssessment.follow_up_recommended.is_(True),
    ]

    if not include_handled:
        predicates.append(EmailAssessment.handled.is_(False))

    rows = list(db.execute(select(EmailAssessment).where(*predicates)).scalars().all())

    return sorted(
        rows,
        key=lambda row: (
            row.follow_up_due_at is None,
            row.follow_up_due_at or datetime.max.replace(tzinfo=UTC),
            row.assessed_at,
        ),
    )


def get_assessment(
    db: Session, assessment_id: uuid.UUID, *, user_id: uuid.UUID
) -> EmailAssessment:
    """One assessment belonging to this user, or raise."""

    assessment = db.execute(
        select(EmailAssessment).where(
            EmailAssessment.id == assessment_id,
            EmailAssessment.user_id == user_id,
        )
    ).scalar_one_or_none()

    if assessment is None:
        raise EmailValidationError(f"Assessment not found: {assessment_id}")

    return assessment


def set_handled(
    db: Session, assessment_id: uuid.UUID, *, handled: bool, user_id: uuid.UUID
) -> EmailAssessment:
    """Mark a follow-up dealt with, or put it back. A person's call, always."""

    assessment = get_assessment(db, assessment_id, user_id=user_id)
    assessment.handled = handled
    db.commit()
    db.refresh(assessment)

    return assessment
