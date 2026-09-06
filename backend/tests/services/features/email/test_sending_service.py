"""Sending: the approval gate, and what happens when a provider says no.

These are the tests that make "no fake sent emails" a property of the code
rather than a promise in a document. Three of them are worth reading in full:

- an unapproved draft is refused **before** a provider is even resolved
- a provider failure leaves the draft `failed`, never `sent`
- with no mailbox configured, sending raises and the draft is untouched
"""

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmailDraftAlreadySentError,
    EmailDraftNotApprovedError,
    EmailProviderNotConfiguredError,
    EmailSendError,
    EmailValidationError,
)
from app.models.email import EmailDraft, EmailDraftStatus
from app.models.user import User
from app.services.features.email import draft_service, sending_service
from tests.support.email import RecordingProvider


def _draft(db: Session, user: User, **overrides: object) -> EmailDraft:
    values: dict = {
        "to_recipients": ["client@example.com"],
        "subject": "Following up",
        "body": "Just checking in.",
    }
    values.update(overrides)

    return draft_service.create_draft(db, user_id=user.id, **values)


def _approved(db: Session, user: User, **overrides: object) -> EmailDraft:
    draft = _draft(db, user, **overrides)

    return draft_service.approve_draft(db, draft.id, user_id=user.id)


# --- the approval gate ---------------------------------------------------


def test_an_unapproved_draft_is_never_sent(
    db_session: Session, test_user: User
) -> None:
    provider = RecordingProvider()
    draft = _draft(db_session, test_user)

    with pytest.raises(EmailDraftNotApprovedError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    db_session.refresh(draft)

    assert provider.sent == []
    assert draft.status is EmailDraftStatus.DRAFT
    assert draft.sent_at is None


def test_a_draft_awaiting_review_is_not_approved(
    db_session: Session, test_user: User
) -> None:
    """`needs_review` means generation finished, not that anyone read it."""

    provider = RecordingProvider()
    draft = _draft(db_session, test_user)
    draft_service.mark_needs_review(db_session, draft.id, user_id=test_user.id)

    with pytest.raises(EmailDraftNotApprovedError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    assert provider.sent == []


def test_a_draft_edited_after_approval_is_refused(
    db_session: Session, test_user: User
) -> None:
    """The whole point of withdrawing approval on edit — the send path has to
    actually honour it."""

    provider = RecordingProvider()
    draft = _approved(db_session, test_user)

    draft_service.update_draft(
        db_session,
        draft.id,
        {"body": "Please wire the deposit to the account below."},
        user_id=test_user.id,
    )

    with pytest.raises(EmailDraftNotApprovedError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    assert provider.sent == []


def test_approval_is_checked_before_the_provider_is_resolved(
    db_session: Session, test_user: User
) -> None:
    """An unapproved draft must be refused for being unapproved even on a fully
    connected system — and a connected system must not be what reveals the
    check exists."""

    draft = _draft(db_session, test_user)

    with pytest.raises(EmailDraftNotApprovedError):
        # No provider passed and none configured: if the order were reversed,
        # this would raise `EmailProviderNotConfiguredError` instead.
        sending_service.send_draft(db_session, draft.id, user_id=test_user.id)


def test_an_approved_draft_is_sent_once(db_session: Session, test_user: User) -> None:
    provider = RecordingProvider()
    draft = _approved(db_session, test_user)

    sent = sending_service.send_draft(
        db_session, draft.id, user_id=test_user.id, provider=provider
    )

    assert len(provider.sent) == 1
    assert sent.status is EmailDraftStatus.SENT
    assert sent.sent_at is not None
    assert sent.provider == "recording"
    assert sent.provider_message_id == "provider-message-1"


def test_a_sent_draft_is_not_sent_twice(db_session: Session, test_user: User) -> None:
    provider = RecordingProvider()
    draft = _approved(db_session, test_user)

    sending_service.send_draft(
        db_session, draft.id, user_id=test_user.id, provider=provider
    )

    with pytest.raises(EmailDraftAlreadySentError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    assert len(provider.sent) == 1


# --- no mailbox ----------------------------------------------------------


@pytest.fixture
def no_mailbox_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee "no mailbox" regardless of the developer's local `.env`.

    Every other test in this module injects a `RecordingProvider`, so none of
    them touches configuration. This one deliberately resolves the provider the
    way production does — that is the whole point of it — which made it the one
    test whose outcome depended on whatever happened to be in `.env`.

    On a machine with `EMAIL_PROVIDER=outlook` and a real
    `EMAIL_MAILBOX_ADDRESS`, the resolver returned a genuine
    `OutlookEmailProvider` and the test then attempted an actual Graph send
    against a real mailbox. It was not merely a wrong assertion: it broke the
    rule in CLAUDE.md §7 that tests never make network calls, and on a developer
    machine with working credentials it could have sent mail.

    The three settings below are the complete set the resolver consults, so
    clearing them is equivalent to an unconfigured deployment without patching
    the resolver itself — the production path stays exactly the one under test.
    """

    from app.config import settings as settings_module

    unset: tuple[tuple[str, object], ...] = (
        ("EMAIL_PROVIDER", None),
        ("EMAIL_MAILBOX_ADDRESS", None),
        # Only exists once per-user mailboxes landed; guarded so this fixture
        # works either side of that change rather than erroring on absence.
        ("EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX", False),
    )

    for name, value in unset:
        if hasattr(settings_module.settings, name):
            monkeypatch.setattr(settings_module.settings, name, value)


def test_with_no_mailbox_configured_sending_refuses(
    db_session: Session, test_user: User, no_mailbox_configured: None
) -> None:
    """There is no simulated provider anywhere in this system, so this is the
    only possible outcome — and the draft keeps its approval, because nothing
    was attempted."""

    draft = _approved(db_session, test_user)

    with pytest.raises(EmailProviderNotConfiguredError):
        sending_service.send_draft(db_session, draft.id, user_id=test_user.id)

    db_session.refresh(draft)

    assert draft.status is EmailDraftStatus.APPROVED
    assert draft.sent_at is None


# --- provider failure ----------------------------------------------------


def test_a_failed_send_is_never_recorded_as_sent(
    db_session: Session, test_user: User
) -> None:
    provider = RecordingProvider(fail_with=EmailSendError("Mailbox rejected it."))
    draft = _approved(db_session, test_user)

    with pytest.raises(EmailSendError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    db_session.refresh(draft)

    assert draft.status is EmailDraftStatus.FAILED
    assert draft.sent_at is None
    assert draft.provider_message_id is None
    assert draft.send_error == "Mailbox rejected it."


def test_a_failed_send_withdraws_approval(db_session: Session, test_user: User) -> None:
    """The next attempt is a new decision, not a retry of an old one."""

    provider = RecordingProvider(fail_with=EmailSendError("Mailbox rejected it."))
    draft = _approved(db_session, test_user)

    with pytest.raises(EmailSendError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    db_session.refresh(draft)

    assert draft.approved_at is None

    with pytest.raises(EmailDraftNotApprovedError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=RecordingProvider()
        )


def test_an_unexpected_provider_crash_is_still_a_failure(
    db_session: Session, test_user: User
) -> None:
    """A provider that raises something nobody anticipated must not leave a
    draft looking sent."""

    provider = RecordingProvider(fail_with=RuntimeError("socket exploded"))
    draft = _approved(db_session, test_user)

    with pytest.raises(EmailSendError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    db_session.refresh(draft)

    assert draft.status is EmailDraftStatus.FAILED
    assert draft.sent_at is None
    # The raw exception text is not surfaced to a caller.
    assert "socket" not in (draft.send_error or "")


def test_a_provider_that_returns_no_message_id_leaves_it_null(
    db_session: Session, test_user: User
) -> None:
    """Graph's `sendMail` returns nothing. Filling the gap with the draft's own
    id would make a local identifier look like a provider's."""

    provider = RecordingProvider(message_id=None)
    draft = _approved(db_session, test_user)

    sent = sending_service.send_draft(
        db_session, draft.id, user_id=test_user.id, provider=provider
    )

    assert sent.status is EmailDraftStatus.SENT
    assert sent.provider_message_id is None


# --- what the provider is handed -----------------------------------------


def test_recipients_subject_body_and_attachments_all_travel(
    db_session: Session, test_user: User
) -> None:
    provider = RecordingProvider()
    draft = _draft(
        db_session,
        test_user,
        to_recipients=["a@x.com"],
        cc_recipients=["b@x.com"],
        bcc_recipients=["c@x.com"],
    )
    draft_service.add_attachment(
        db_session,
        draft.id,
        filename="scope.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4 scope",
        user_id=test_user.id,
    )
    draft_service.approve_draft(db_session, draft.id, user_id=test_user.id)

    sending_service.send_draft(
        db_session, draft.id, user_id=test_user.id, provider=provider
    )

    outgoing = provider.sent[0]

    assert outgoing.to_recipients == ["a@x.com"]
    assert outgoing.cc_recipients == ["b@x.com"]
    assert outgoing.bcc_recipients == ["c@x.com"]
    assert outgoing.subject == "Following up"
    assert outgoing.attachments[0].filename == "scope.pdf"
    assert outgoing.attachments[0].content == b"%PDF-1.4 scope"


def test_a_reply_carries_the_message_it_replies_to(
    db_session: Session, test_user: User
) -> None:
    """Without this the provider starts a new conversation, and the recipient's
    client shows the reply detached from the exchange."""

    provider = RecordingProvider()
    draft = _approved(db_session, test_user, in_reply_to_message_id="original-1")

    sending_service.send_draft(
        db_session, draft.id, user_id=test_user.id, provider=provider
    )

    assert provider.sent[0].in_reply_to_message_id == "original-1"


def test_another_users_draft_cannot_be_sent(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    from app.core.exceptions import EmailDraftNotFoundError

    provider = RecordingProvider()
    draft = _approved(db_session, test_user)

    with pytest.raises(EmailDraftNotFoundError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user_b.id, provider=provider
        )

    assert provider.sent == []


def test_a_draft_that_lost_its_recipients_is_refused(
    db_session: Session, test_user: User
) -> None:
    provider = RecordingProvider()
    draft = _approved(db_session, test_user)
    # Bypassing the service on purpose: this asserts the send path has its own
    # guard rather than relying on approval having checked once.
    draft.to_recipients = []
    db_session.commit()

    with pytest.raises(EmailValidationError):
        sending_service.send_draft(
            db_session, draft.id, user_id=test_user.id, provider=provider
        )

    assert provider.sent == []
