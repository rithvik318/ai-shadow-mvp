"""Drafts and their attachments: everything about an email before it leaves.

This module owns one invariant, and it is the reason the Email Agent is safe to
point at a real mailbox:

    **Changing a draft revokes its approval.**

Approval is a person saying "send *this text*". If the text can change
afterwards, approval means "send whatever is in this row at send time", which
is not a review — it is a rubber stamp with a race in it. So every mutation
here funnels through `_touch`, which clears `approved_at` and returns the draft
to `draft` unless it has already been sent. A sent draft is immutable: the
message is gone, and editing the record of it would make the record a lie.

Nothing in this module talks to a provider, and nothing in it sends. Sending
lives in `sending_service`, which is the only module allowed to move a draft to
`sent` — and only on a provider's word.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.core.exceptions import (
    EmailAttachmentTooLargeError,
    EmailDraftAlreadySentError,
    EmailDraftNotFoundError,
    EmailValidationError,
    EmptyAttachmentError,
    TooManyAttachmentsError,
)
from app.models.email import EmailAttachment, EmailDraft, EmailDraftStatus

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = (
    "to_recipients",
    "cc_recipients",
    "bcc_recipients",
    "subject",
    "body",
    "template_id",
    "in_reply_to_message_id",
)

# Statuses a person is still working through. Everything else is terminal or
# in flight, and neither is editable.
OPEN_STATUSES = frozenset(
    {
        EmailDraftStatus.DRAFT,
        EmailDraftStatus.NEEDS_REVIEW,
        EmailDraftStatus.APPROVED,
        EmailDraftStatus.FAILED,
    }
)

# A pragmatic shape check, not RFC 5322. The point is to catch "jsmith" and
# "john smith" before they reach a provider, not to adjudicate what a legal
# address is — providers reject their own invalid addresses far better than a
# regular expression can, and a strict pattern here would reject valid ones.
_MIN_ADDRESS_PARTS = 2


def normalise_recipients(values: list[str] | None) -> list[str]:
    """Trim, drop blanks, and remove duplicates while keeping order.

    Order is kept because recipient order is meaningful to the people reading
    it — the first name on a To line is usually the person being asked.
    """

    seen: list[str] = []

    for value in values or []:
        address = (value or "").strip()

        if address and address.lower() not in {item.lower() for item in seen}:
            seen.append(address)

    return seen


def validate_recipients(values: list[str], *, field: str) -> None:
    """Reject anything that plainly is not an address.

    Whitespace *inside* an address is refused as well as around it.
    `normalise_recipients` strips the outside, so what reaches here with a
    space in it is a genuinely malformed address — usually a name that was
    typed where an address belongs ("john smith@x.com"), or two addresses that
    were pasted separated by a space and never split. Neither is deliverable,
    and both are far better caught here than by a provider at send time.
    """

    for address in values:
        if any(character.isspace() for character in address):
            raise EmailValidationError(
                f"{address!r} in {field} is not an email address: an address "
                "cannot contain spaces."
            )

        if "@" not in address or len(address.split("@")) != _MIN_ADDRESS_PARTS:
            raise EmailValidationError(
                f"{address!r} in {field} is not an email address."
            )

        local, domain = address.split("@")

        if not local or "." not in domain or domain.startswith("."):
            raise EmailValidationError(
                f"{address!r} in {field} is not an email address."
            )


def _touch(draft: EmailDraft) -> None:
    """Record that the content changed, and withdraw any approval.

    The whole human-in-the-loop guarantee reduces to this function being called
    by every mutation. `failed` is reset to `draft` too: correcting a draft that
    a provider refused should clear the old error rather than leave it beside
    new text it no longer describes.
    """

    if draft.status is EmailDraftStatus.SENT:
        raise EmailDraftAlreadySentError(
            "That email has already been sent and cannot be changed."
        )

    if draft.status is EmailDraftStatus.SENDING:
        raise EmailValidationError(
            "That draft is being sent. Wait for the result before editing it."
        )

    draft.status = EmailDraftStatus.DRAFT
    draft.approved_at = None
    draft.send_error = None


def create_draft(
    db: Session,
    *,
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
    subject: str = "",
    body: str = "",
    template_id: uuid.UUID | None = None,
    in_reply_to_message_id: str | None = None,
    provider_thread_id: str | None = None,
    generated_by_ai: bool = False,
    status: EmailDraftStatus = EmailDraftStatus.DRAFT,
    user_id: uuid.UUID,
) -> EmailDraft:
    """Start a draft.

    Recipients are optional: a draft generated from an instruction often has
    none yet, and refusing to save it would mean losing the generated text
    while somebody looks up an address. They are required at *send* time, which
    is where the requirement actually bites.
    """

    to_list = normalise_recipients(to_recipients)
    cc_list = normalise_recipients(cc_recipients)
    bcc_list = normalise_recipients(bcc_recipients)

    validate_recipients(to_list, field="to")
    validate_recipients(cc_list, field="cc")
    validate_recipients(bcc_list, field="bcc")

    draft = EmailDraft(
        user_id=user_id,
        to_recipients=to_list,
        cc_recipients=cc_list,
        bcc_recipients=bcc_list,
        subject=subject,
        body=body,
        template_id=template_id,
        in_reply_to_message_id=in_reply_to_message_id,
        provider_thread_id=provider_thread_id,
        generated_by_ai=generated_by_ai,
        status=status,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    logger.info(
        "email_draft_created",
        extra={
            "user_id": str(user_id),
            "draft_id": str(draft.id),
            "generated_by_ai": generated_by_ai,
        },
    )

    return draft


def list_drafts(
    db: Session,
    *,
    status: EmailDraftStatus | None = None,
    user_id: uuid.UUID,
) -> list[EmailDraft]:
    """This user's drafts, most recently touched first."""

    predicates = [EmailDraft.user_id == user_id]

    if status is not None:
        predicates.append(EmailDraft.status == status)

    return list(
        db.execute(
            select(EmailDraft)
            .options(selectinload(EmailDraft.attachments))
            .where(*predicates)
            .order_by(EmailDraft.updated_at.desc(), EmailDraft.id)
        )
        .scalars()
        .all()
    )


def get_draft(db: Session, draft_id: uuid.UUID, *, user_id: uuid.UUID) -> EmailDraft:
    """Return this user's draft, or raise. Another user's is a 404."""

    draft = db.execute(
        select(EmailDraft)
        .options(selectinload(EmailDraft.attachments))
        .where(EmailDraft.id == draft_id, EmailDraft.user_id == user_id)
    ).scalar_one_or_none()

    if draft is None:
        raise EmailDraftNotFoundError(f"Email draft not found: {draft_id}")

    return draft


def update_draft(
    db: Session,
    draft_id: uuid.UUID,
    values: dict[str, object],
    *,
    user_id: uuid.UUID,
) -> EmailDraft:
    """Change the fields supplied — and withdraw approval, always.

    Even a change that looks harmless withdraws it. There is no list of "safe"
    edits, because deciding which edits are safe is precisely the judgement the
    human review exists to make.
    """

    draft = get_draft(db, draft_id, user_id=user_id)

    # Everything is validated before anything is assigned. A half-applied
    # update that then raised would leave the row mutated in the session, and
    # the next query's autoflush would write it — so a rejected edit would
    # still have withdrawn the approval.
    cleaned: dict[str, object] = {}

    for field, value in values.items():
        if field not in EDITABLE_FIELDS:
            continue

        if field.endswith("_recipients"):
            addresses = normalise_recipients(value if isinstance(value, list) else [])
            validate_recipients(addresses, field=field.removesuffix("_recipients"))
            cleaned[field] = addresses
        else:
            cleaned[field] = value

    _touch(draft)

    for field, value in cleaned.items():
        setattr(draft, field, value)

    db.commit()
    db.refresh(draft)

    return draft


def delete_draft(db: Session, draft_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Delete a draft and its attachments.

    A sent draft can be deleted: the record is this system's, and removing it
    does not unsend anything. Editing one is refused, because that would
    misrepresent what was sent; deleting one simply forgets it.
    """

    draft = get_draft(db, draft_id, user_id=user_id)
    db.delete(draft)
    db.commit()

    logger.info(
        "email_draft_deleted",
        extra={"user_id": str(user_id), "draft_id": str(draft_id)},
    )


def mark_needs_review(
    db: Session, draft_id: uuid.UUID, *, user_id: uuid.UUID
) -> EmailDraft:
    """Say that generation finished and a person has not read it yet."""

    draft = get_draft(db, draft_id, user_id=user_id)
    _touch(draft)
    draft.status = EmailDraftStatus.NEEDS_REVIEW
    db.commit()
    db.refresh(draft)

    return draft


def approve_draft(
    db: Session, draft_id: uuid.UUID, *, user_id: uuid.UUID
) -> EmailDraft:
    """Record that a person read this draft and wants it sent.

    Approval does not send. It is a separate, recorded act, and the send
    endpoint refuses anything that has not been through it — so "approve" and
    "send" are two deliberate decisions rather than one button that does both.

    A draft with no recipients cannot be approved: the review being performed
    is of a message *to somebody*, and approving one addressed to nobody would
    approve something that cannot be checked.
    """

    draft = get_draft(db, draft_id, user_id=user_id)

    if draft.status is EmailDraftStatus.SENT:
        raise EmailDraftAlreadySentError("That email has already been sent.")

    if draft.status is EmailDraftStatus.SENDING:
        raise EmailValidationError("That draft is already being sent.")

    if not draft.to_recipients:
        raise EmailValidationError(
            "Add at least one recipient before approving this draft."
        )

    if not (draft.subject or "").strip():
        raise EmailValidationError("Add a subject before approving this draft.")

    if not (draft.body or "").strip():
        raise EmailValidationError("This draft has no message body.")

    draft.status = EmailDraftStatus.APPROVED
    draft.approved_at = datetime.now(UTC)
    draft.send_error = None
    db.commit()
    db.refresh(draft)

    logger.info(
        "email_draft_approved",
        extra={
            "user_id": str(user_id),
            "draft_id": str(draft.id),
            "recipients": len(draft.to_recipients),
        },
    )

    return draft


# --- attachments ---------------------------------------------------------


def add_attachment(
    db: Session,
    draft_id: uuid.UUID,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    user_id: uuid.UUID,
) -> EmailAttachment:
    """Attach a file to a draft, within the configured limits.

    Attaching is a content change, so it withdraws approval like any other —
    otherwise a file could be added to an email after somebody approved it
    without one.

    No format restriction. An email attachment is whatever a person means to
    send, and the ingestion pipeline's supported-format list is about text
    extraction, which has nothing to do with this.
    """

    draft = get_draft(db, draft_id, user_id=user_id)

    if not data:
        raise EmptyAttachmentError(f"{filename!r} is empty, so it was not attached.")

    if len(data) > settings.EMAIL_MAX_ATTACHMENT_BYTES:
        raise EmailAttachmentTooLargeError(
            f"{filename!r} is {len(data)} bytes, which exceeds the "
            f"{settings.EMAIL_MAX_ATTACHMENT_BYTES}-byte limit for one "
            "attachment."
        )

    existing = db.execute(
        select(func.count())
        .select_from(EmailAttachment)
        .where(EmailAttachment.draft_id == draft.id)
    ).scalar_one()

    if existing >= settings.EMAIL_MAX_ATTACHMENTS_PER_DRAFT:
        raise TooManyAttachmentsError(
            f"A draft may carry at most "
            f"{settings.EMAIL_MAX_ATTACHMENTS_PER_DRAFT} attachments."
        )

    _touch(draft)

    attachment = EmailAttachment(
        draft_id=draft.id,
        filename=(filename or "attachment").strip() or "attachment",
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        content=data,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)

    logger.info(
        "email_attachment_added",
        extra={
            "user_id": str(user_id),
            "draft_id": str(draft.id),
            "size_bytes": attachment.size_bytes,
        },
    )

    return attachment


def remove_attachment(
    db: Session,
    draft_id: uuid.UUID,
    attachment_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
) -> None:
    """Detach a file. Also a content change, so also withdraws approval."""

    draft = get_draft(db, draft_id, user_id=user_id)

    attachment = db.execute(
        select(EmailAttachment).where(
            EmailAttachment.id == attachment_id,
            EmailAttachment.draft_id == draft.id,
        )
    ).scalar_one_or_none()

    if attachment is None:
        raise EmailDraftNotFoundError(f"Attachment not found: {attachment_id}")

    _touch(draft)
    db.delete(attachment)
    db.commit()
