"""Drafts and attachments — and the invariant the whole module exists for.

The tests that matter most here are the approval ones. Every other assertion is
ordinary CRUD; `test_editing_an_approved_draft_withdraws_approval` and its
neighbours are what stop an approved draft from being a rubber stamp with a
race in it.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmailAttachmentTooLargeError,
    EmailDraftAlreadySentError,
    EmailDraftNotFoundError,
    EmailValidationError,
    EmptyAttachmentError,
    TooManyAttachmentsError,
)
from app.models.email import EmailDraft, EmailDraftStatus
from app.models.user import User
from app.services.features.email import draft_service


def _draft(db: Session, user: User, **overrides: object) -> EmailDraft:
    values: dict = {
        "to_recipients": ["client@example.com"],
        "subject": "Following up",
        "body": "Just checking in on the proposal.",
    }
    values.update(overrides)

    return draft_service.create_draft(db, user_id=user.id, **values)


def _approved(db: Session, user: User, **overrides: object) -> EmailDraft:
    draft = _draft(db, user, **overrides)

    return draft_service.approve_draft(db, draft.id, user_id=user.id)


# --- ownership -----------------------------------------------------------


def test_a_draft_belongs_to_its_creator(db_session: Session, test_user: User) -> None:
    draft = _draft(db_session, test_user)

    assert draft.user_id == test_user.id
    assert draft.status is EmailDraftStatus.DRAFT


def test_another_users_draft_is_unreachable(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """Unsent mail is private. Another person's draft is missing, not
    forbidden — a 403 would confirm it exists."""

    draft = _draft(db_session, test_user)

    assert draft_service.list_drafts(db_session, user_id=test_user_b.id) == []

    with pytest.raises(EmailDraftNotFoundError):
        draft_service.get_draft(db_session, draft.id, user_id=test_user_b.id)

    with pytest.raises(EmailDraftNotFoundError):
        draft_service.update_draft(
            db_session, draft.id, {"body": "changed"}, user_id=test_user_b.id
        )

    with pytest.raises(EmailDraftNotFoundError):
        draft_service.delete_draft(db_session, draft.id, user_id=test_user_b.id)


def test_a_missing_draft_is_not_found(db_session: Session, test_user: User) -> None:
    with pytest.raises(EmailDraftNotFoundError):
        draft_service.get_draft(db_session, uuid.uuid4(), user_id=test_user.id)


# --- recipients ----------------------------------------------------------


def test_recipients_are_deduplicated_and_keep_their_order() -> None:
    """Order is meaningful to the people reading it: the first name on a To
    line is usually the person being asked."""

    result = draft_service.normalise_recipients(
        [" a@x.com ", "b@x.com", "A@X.com", "", "   "]
    )

    assert result == ["a@x.com", "b@x.com"]


@pytest.mark.parametrize(
    "address",
    [
        "jsmith",
        # Whitespace anywhere. `normalise_recipients` strips the outside, so a
        # space that survives to validation is a name typed where an address
        # belongs, or two addresses pasted without a separator. Neither is
        # deliverable, and both used to be accepted.
        "john smith@x.com",
        "a@x .com",
        "a\tb@x.com",
        "a@x.com b@x.com",
        "a@",
        "@x.com",
        "a@x",
        "a@.com",
        "a@b@c.com",
    ],
)
def test_a_malformed_recipient_is_refused(
    db_session: Session, test_user: User, address: str
) -> None:
    with pytest.raises(EmailValidationError):
        _draft(db_session, test_user, to_recipients=[address])


def test_a_draft_may_be_saved_with_no_recipients(
    db_session: Session, test_user: User
) -> None:
    """A generated draft often has none yet. Refusing to save it would throw
    away the text while somebody looks up an address."""

    draft = _draft(db_session, test_user, to_recipients=[])

    assert draft.to_recipients == []


# --- the approval invariant ----------------------------------------------


def test_approving_records_when_and_by_whom(
    db_session: Session, test_user: User
) -> None:
    draft = _approved(db_session, test_user)

    assert draft.status is EmailDraftStatus.APPROVED
    assert draft.approved_at is not None


def test_a_draft_with_no_recipients_cannot_be_approved(
    db_session: Session, test_user: User
) -> None:
    """The review being performed is of a message *to somebody*."""

    draft = _draft(db_session, test_user, to_recipients=[])

    with pytest.raises(EmailValidationError):
        draft_service.approve_draft(db_session, draft.id, user_id=test_user.id)


def test_a_draft_with_no_subject_or_body_cannot_be_approved(
    db_session: Session, test_user: User
) -> None:
    blank_subject = _draft(db_session, test_user, subject="   ")
    blank_body = _draft(db_session, test_user, body="")

    with pytest.raises(EmailValidationError):
        draft_service.approve_draft(db_session, blank_subject.id, user_id=test_user.id)

    with pytest.raises(EmailValidationError):
        draft_service.approve_draft(db_session, blank_body.id, user_id=test_user.id)


def test_editing_an_approved_draft_withdraws_approval(
    db_session: Session, test_user: User
) -> None:
    """The invariant. Without it, approval would mean "send whatever is in this
    row at send time" rather than "send this text"."""

    draft = _approved(db_session, test_user)

    edited = draft_service.update_draft(
        db_session,
        draft.id,
        {"body": "Actually, please wire the deposit."},
        user_id=test_user.id,
    )

    assert edited.status is EmailDraftStatus.DRAFT
    assert edited.approved_at is None


def test_changing_recipients_withdraws_approval(
    db_session: Session, test_user: User
) -> None:
    """Adding a recipient to an approved email is exactly the change a review
    is supposed to catch."""

    draft = _approved(db_session, test_user)

    edited = draft_service.update_draft(
        db_session,
        draft.id,
        {"to_recipients": ["client@example.com", "competitor@example.com"]},
        user_id=test_user.id,
    )

    assert edited.status is EmailDraftStatus.DRAFT
    assert edited.approved_at is None


def test_attaching_a_file_withdraws_approval(
    db_session: Session, test_user: User
) -> None:
    draft = _approved(db_session, test_user)

    draft_service.add_attachment(
        db_session,
        draft.id,
        filename="terms.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4 terms",
        user_id=test_user.id,
    )
    db_session.refresh(draft)

    assert draft.status is EmailDraftStatus.DRAFT
    assert draft.approved_at is None


def test_removing_an_attachment_withdraws_approval(
    db_session: Session, test_user: User
) -> None:
    draft = _draft(db_session, test_user)
    attachment = draft_service.add_attachment(
        db_session,
        draft.id,
        filename="terms.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4 terms",
        user_id=test_user.id,
    )
    draft_service.approve_draft(db_session, draft.id, user_id=test_user.id)

    draft_service.remove_attachment(
        db_session, draft.id, attachment.id, user_id=test_user.id
    )
    db_session.refresh(draft)

    assert draft.status is EmailDraftStatus.DRAFT
    assert draft.approved_at is None


def test_a_sent_draft_cannot_be_edited(db_session: Session, test_user: User) -> None:
    """The message is gone. Editing the record of it would make the record a
    lie about what somebody actually received."""

    draft = _draft(db_session, test_user)
    draft.status = EmailDraftStatus.SENT
    db_session.commit()

    with pytest.raises(EmailDraftAlreadySentError):
        draft_service.update_draft(
            db_session, draft.id, {"body": "rewritten"}, user_id=test_user.id
        )


def test_a_sent_draft_cannot_be_approved_again(
    db_session: Session, test_user: User
) -> None:
    draft = _draft(db_session, test_user)
    draft.status = EmailDraftStatus.SENT
    db_session.commit()

    with pytest.raises(EmailDraftAlreadySentError):
        draft_service.approve_draft(db_session, draft.id, user_id=test_user.id)


def test_a_sending_draft_cannot_be_edited(db_session: Session, test_user: User) -> None:
    draft = _draft(db_session, test_user)
    draft.status = EmailDraftStatus.SENDING
    db_session.commit()

    with pytest.raises(EmailValidationError):
        draft_service.update_draft(
            db_session, draft.id, {"body": "rewritten"}, user_id=test_user.id
        )


def test_a_sent_draft_may_still_be_deleted(
    db_session: Session, test_user: User
) -> None:
    """Deleting forgets this system's record; it does not unsend anything."""

    draft = _draft(db_session, test_user)
    draft.status = EmailDraftStatus.SENT
    db_session.commit()

    draft_service.delete_draft(db_session, draft.id, user_id=test_user.id)

    assert draft_service.list_drafts(db_session, user_id=test_user.id) == []


def test_correcting_a_failed_draft_clears_the_old_error(
    db_session: Session, test_user: User
) -> None:
    """A stale error beside new text describes something that is no longer
    there."""

    draft = _draft(db_session, test_user)
    draft.status = EmailDraftStatus.FAILED
    draft.send_error = "Mailbox rejected the recipient."
    db_session.commit()

    edited = draft_service.update_draft(
        db_session,
        draft.id,
        {"to_recipients": ["right@example.com"]},
        user_id=test_user.id,
    )

    assert edited.status is EmailDraftStatus.DRAFT
    assert edited.send_error is None


# --- attachments ---------------------------------------------------------


def test_an_attachment_keeps_its_name_type_size_and_bytes(
    db_session: Session, test_user: User
) -> None:
    draft = _draft(db_session, test_user)

    attachment = draft_service.add_attachment(
        db_session,
        draft.id,
        filename="capabilities.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4 capabilities",
        user_id=test_user.id,
    )

    assert attachment.filename == "capabilities.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.size_bytes == len(b"%PDF-1.4 capabilities")
    assert attachment.content == b"%PDF-1.4 capabilities"


def test_an_empty_attachment_is_refused(db_session: Session, test_user: User) -> None:
    """Almost always a failed read on the client. Discovering that at send time
    would be too late."""

    draft = _draft(db_session, test_user)

    with pytest.raises(EmptyAttachmentError):
        draft_service.add_attachment(
            db_session,
            draft.id,
            filename="empty.pdf",
            content_type="application/pdf",
            data=b"",
            user_id=test_user.id,
        )


def test_an_oversized_attachment_is_refused(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_MAX_ATTACHMENT_BYTES", 8)
    draft = _draft(db_session, test_user)

    with pytest.raises(EmailAttachmentTooLargeError):
        draft_service.add_attachment(
            db_session,
            draft.id,
            filename="big.pdf",
            content_type="application/pdf",
            data=b"far too many bytes",
            user_id=test_user.id,
        )


def test_too_many_attachments_are_refused(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_MAX_ATTACHMENTS_PER_DRAFT", 2)
    draft = _draft(db_session, test_user)

    for index in range(2):
        draft_service.add_attachment(
            db_session,
            draft.id,
            filename=f"file-{index}.txt",
            content_type="text/plain",
            data=b"bytes",
            user_id=test_user.id,
        )

    with pytest.raises(TooManyAttachmentsError):
        draft_service.add_attachment(
            db_session,
            draft.id,
            filename="one-too-many.txt",
            content_type="text/plain",
            data=b"bytes",
            user_id=test_user.id,
        )


def test_an_attachment_defaults_its_content_type(
    db_session: Session, test_user: User
) -> None:
    """A browser that sends no content type must not produce a NULL column."""

    draft = _draft(db_session, test_user)

    attachment = draft_service.add_attachment(
        db_session,
        draft.id,
        filename="notes",
        content_type=None,
        data=b"bytes",
        user_id=test_user.id,
    )

    assert attachment.content_type == "application/octet-stream"


def test_attachments_cannot_be_added_to_another_users_draft(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    draft = _draft(db_session, test_user)

    with pytest.raises(EmailDraftNotFoundError):
        draft_service.add_attachment(
            db_session,
            draft.id,
            filename="x.txt",
            content_type="text/plain",
            data=b"bytes",
            user_id=test_user_b.id,
        )


def test_deleting_a_draft_removes_its_attachments(
    db_session: Session, test_user: User
) -> None:
    from sqlalchemy import func, select

    from app.models.email import EmailAttachment

    draft = _draft(db_session, test_user)
    draft_service.add_attachment(
        db_session,
        draft.id,
        filename="x.txt",
        content_type="text/plain",
        data=b"bytes",
        user_id=test_user.id,
    )

    draft_service.delete_draft(db_session, draft.id, user_id=test_user.id)

    remaining = db_session.execute(
        select(func.count()).select_from(EmailAttachment)
    ).scalar_one()

    assert remaining == 0


def test_a_rejected_edit_does_not_withdraw_approval(
    db_session: Session, test_user: User
) -> None:
    """A half-applied update would have mutated the row before raising, and the
    next query's autoflush would have written it — so a rejected edit would
    still have cost the approval."""

    draft = _approved(db_session, test_user)

    with pytest.raises(EmailValidationError):
        draft_service.update_draft(
            db_session,
            draft.id,
            {"subject": "New subject", "to_recipients": ["not-an-address"]},
            user_id=test_user.id,
        )

    db_session.rollback()
    reloaded = draft_service.get_draft(db_session, draft.id, user_id=test_user.id)

    assert reloaded.status is EmailDraftStatus.APPROVED
    assert reloaded.subject == "Following up"


@pytest.mark.parametrize(
    "address",
    [
        "a@x.com",
        "a.b+tag@sub.example.co.uk",
        "first.last@example-company.com",
        "UPPER@Example.COM",
    ],
)
def test_a_well_formed_recipient_is_accepted(
    db_session: Session, test_user: User, address: str
) -> None:
    """The other half of the whitespace fix: tightening validation must not
    start refusing addresses people actually use."""

    draft = _draft(db_session, test_user, to_recipients=[address])

    assert draft.to_recipients == [address]


def test_surrounding_whitespace_is_trimmed_rather_than_refused(
    db_session: Session, test_user: User
) -> None:
    """A pasted address with a trailing space is a normal thing to type, and
    `normalise_recipients` handles it before validation ever sees it."""

    draft = _draft(db_session, test_user, to_recipients=["  a@x.com  "])

    assert draft.to_recipients == ["a@x.com"]
