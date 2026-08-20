"""Reading a real mailbox, and pairing what is there with what triage said.

The bridge between the provider boundary and the stored assessments, and
nothing more: it fetches messages, looks up any assessment already held for
each, and hands the pair back. It does not classify — that is
`triage_service` — and it does not know what Graph is.

Every function here needs a provider, so every function here fails on a
deployment with no mailbox. That is the correct shape: this module is the only
part of the Email Agent that *requires* a connection, and composing, templates
and drafts all work without it.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.email import EmailAssessment
from app.services.email.provider.base import EmailMessage, EmailProvider
from app.services.email.provider.registry import get_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriagedMessage:
    """One real message, with this user's assessment of it if there is one.

    `assessment` is None for a message nobody has triaged yet, and the UI shows
    it as untriaged rather than guessing a category. Triage costs a model call
    per message, so it is requested rather than run over an inbox on sight.
    """

    message: EmailMessage
    assessment: EmailAssessment | None


def _assessments_for(
    db: Session, *, provider: str, message_ids: list[str], user_id: uuid.UUID
) -> dict[str, EmailAssessment]:
    """Look up every stored assessment for a page of messages in one query."""

    if not message_ids:
        return {}

    rows = (
        db.execute(
            select(EmailAssessment).where(
                EmailAssessment.user_id == user_id,
                EmailAssessment.provider == provider,
                EmailAssessment.provider_message_id.in_(message_ids),
            )
        )
        .scalars()
        .all()
    )

    return {row.provider_message_id: row for row in rows}


def list_inbox(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int | None = None,
    folder: str | None = None,
    provider: EmailProvider | None = None,
) -> list[TriagedMessage]:
    """Recent messages from the connected mailbox, newest first.

    Raises `EmailProviderNotConfiguredError` when no mailbox is connected. The
    API turns that into a 409 and the UI into a setup notice — which is what
    the person actually needs to see, and is why no empty list is returned to
    stand in for "not connected".
    """

    mailbox = provider if provider is not None else get_provider()
    messages = mailbox.list_messages(
        limit=limit or settings.EMAIL_INBOX_PAGE_SIZE, folder=folder
    )

    assessments = _assessments_for(
        db,
        provider=mailbox.name,
        message_ids=[message.message_id for message in messages],
        user_id=user_id,
    )

    return [
        TriagedMessage(message=message, assessment=assessments.get(message.message_id))
        for message in messages
    ]


def get_message(
    db: Session,
    message_id: str,
    *,
    user_id: uuid.UUID,
    provider: EmailProvider | None = None,
) -> TriagedMessage:
    """One message with its body, and this user's assessment of it if any."""

    mailbox = provider if provider is not None else get_provider()
    message = mailbox.get_message(message_id)

    assessments = _assessments_for(
        db,
        provider=mailbox.name,
        message_ids=[message.message_id],
        user_id=user_id,
    )

    return TriagedMessage(
        message=message, assessment=assessments.get(message.message_id)
    )


def get_thread(
    thread_id: str, *, provider: EmailProvider | None = None
) -> list[EmailMessage]:
    """Every message in a conversation, oldest first.

    No database argument: a thread is read straight from the provider, and
    thread summaries are deliberately not stored — a thread grows, and a stored
    summary of one is wrong as soon as somebody replies.
    """

    mailbox = provider if provider is not None else get_provider()

    return mailbox.get_thread(thread_id)
