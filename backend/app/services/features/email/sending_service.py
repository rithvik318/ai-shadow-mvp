"""The only module that may send an email, and the gate it has to pass.

Three rules, and each of them is a refusal rather than a feature:

1. **A draft that nobody approved is not sent.** `EmailDraftStatus.APPROVED` is
   set only by `draft_service.approve_draft`, and any edit to a draft clears
   it. So the text a provider receives is always text a person read and then
   said yes to — not text that was approved and subsequently changed.
2. **A model never reaches this module.** Nothing here calls the LLM layer, and
   no generation path calls `send`. The agent writes; a person approves; this
   sends. There is no arrangement of the code in which those collapse into one
   step.
3. **`sent` is written only on a provider's receipt.** Every failure path sets
   `failed` and records why. There is no provider that fabricates success, so
   there is no way for this function to report a send that did not happen.

The `sending` status exists for the window between handing the message over and
learning what happened. If the process dies in that window the draft is left
`sending`, which is honest — this system genuinely does not know whether the
message went, and guessing either way would be worse than saying so.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmailDraftAlreadySentError,
    EmailDraftNotApprovedError,
    EmailProviderError,
    EmailSendError,
    EmailValidationError,
)
from app.models.email import EmailDraft, EmailDraftStatus
from app.services.email.provider.base import (
    EmailProvider,
    OutgoingAttachment,
    OutgoingEmail,
)
from app.services.email.provider.registry import get_provider
from app.services.features.email import draft_service

logger = logging.getLogger(__name__)


def to_outgoing(draft: EmailDraft) -> OutgoingEmail:
    """Turn a stored draft into the provider-neutral outgoing shape.

    Pure, and public so a test can assert the conversion — including that
    attachment bytes travel — without a provider.
    """

    return OutgoingEmail(
        to_recipients=list(draft.to_recipients or []),
        cc_recipients=list(draft.cc_recipients or []),
        bcc_recipients=list(draft.bcc_recipients or []),
        subject=draft.subject or "",
        body=draft.body or "",
        attachments=[
            OutgoingAttachment(
                filename=attachment.filename,
                content_type=attachment.content_type,
                content=attachment.content,
            )
            for attachment in draft.attachments
        ],
        in_reply_to_message_id=draft.in_reply_to_message_id,
    )


def send_draft(
    db: Session,
    draft_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    provider: EmailProvider | None = None,
) -> EmailDraft:
    """Send an approved draft through the configured provider.

    `provider` is injectable so tests can supply one that fails, or one that
    records what it was given. In production it is None and the configured
    provider is resolved — which raises `EmailProviderNotConfiguredError` when
    no mailbox is connected, so "send" on an unconnected deployment is a clear
    409 and never a silent success.

    Order matters and is deliberate: approval is checked *before* the provider
    is resolved. An unapproved draft must be refused for being unapproved even
    on a fully connected system, and a connected system must not be the thing
    that reveals the check exists.
    """

    draft = draft_service.get_draft(db, draft_id, user_id=user_id)

    if draft.status is EmailDraftStatus.SENT:
        raise EmailDraftAlreadySentError("That email has already been sent.")

    if draft.status is EmailDraftStatus.SENDING:
        raise EmailValidationError("That draft is already being sent.")

    if draft.status is not EmailDraftStatus.APPROVED or draft.approved_at is None:
        raise EmailDraftNotApprovedError(
            "This draft has not been approved. Review it and approve it before "
            "sending — an email is never sent on the assistant's own account."
        )

    if not draft.to_recipients:
        raise EmailValidationError("This draft has no recipients.")

    mailbox = provider if provider is not None else get_provider()

    draft.status = EmailDraftStatus.SENDING
    draft.send_error = None
    db.commit()

    try:
        receipt = mailbox.send(to_outgoing(draft))
    except EmailProviderError as exc:
        # Recorded as failed, never as sent. The draft keeps its content and
        # can be corrected and approved again — approval was consumed by the
        # attempt, which is correct: the next attempt is a new decision.
        draft.status = EmailDraftStatus.FAILED
        draft.approved_at = None
        draft.send_error = str(exc)
        db.commit()
        db.refresh(draft)

        logger.warning(
            "email_send_failed",
            extra={
                "user_id": str(user_id),
                "draft_id": str(draft.id),
                "error": type(exc).__name__,
            },
        )

        raise
    except Exception as exc:  # noqa: BLE001 - an unknown failure is still a failure
        draft.status = EmailDraftStatus.FAILED
        draft.approved_at = None
        draft.send_error = "The mailbox provider failed unexpectedly."
        db.commit()

        logger.exception(
            "email_send_crashed",
            extra={"user_id": str(user_id), "draft_id": str(draft.id)},
        )

        raise EmailSendError(
            "The mailbox provider failed unexpectedly and the message was not "
            "confirmed sent."
        ) from exc

    draft.status = EmailDraftStatus.SENT
    draft.sent_at = receipt.sent_at or datetime.now(UTC)
    draft.provider = receipt.provider
    # Left as None when the provider did not return one — Graph's `sendMail`
    # does not. An invented id would look exactly like a real one.
    draft.provider_message_id = receipt.message_id
    draft.provider_thread_id = receipt.thread_id or draft.provider_thread_id
    draft.send_error = None
    db.commit()
    db.refresh(draft)

    logger.info(
        "email_sent",
        extra={
            "user_id": str(user_id),
            "draft_id": str(draft.id),
            "provider": receipt.provider,
            "recipients": len(draft.to_recipients),
            "attachments": len(draft.attachments),
        },
    )

    return draft
