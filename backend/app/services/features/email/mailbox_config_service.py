"""Who owns which mailbox, and how a provider is built for one person.

The module that turns "the Email Agent's mailbox" from a deployment setting
into a per-user fact. Everything above it asks for *this user's* provider and
gets one bound to *this user's* address; nothing above it reads
`EMAIL_MAILBOX_ADDRESS`.

Two rules hold the isolation up, and both are enforced here rather than
trusted:

**Every function takes `user_id` and filters on it.** There is no "get the
mailbox" without a person attached, so there is no shape of call that could
return somebody else's.

**The fallback never becomes a shared mailbox by accident.**
`EMAIL_MAILBOX_ADDRESS` still works, but only when
`EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX` is switched on deliberately — otherwise a
user with no mailbox of their own is *not connected*, which is the honest
answer. Silently handing every user the same inbox would look like the feature
working while leaking one person's mail to everybody.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import EmailProviderNotConfiguredError, EmailValidationError
from app.models.email import UserMailbox
from app.services.email.provider.base import EmailProvider, ProviderStatus
from app.services.email.provider.registry import (
    configured_provider_name,
    get_provider,
    status_for,
    supported_provider_names,
)
from app.services.features.email.address import parse_address

logger = logging.getLogger(__name__)

NO_MAILBOX_DETAIL = (
    "No mailbox is connected for this user. Drafting, rewriting, templates and "
    "saved drafts all work without one; listing an inbox and sending do not. "
    "Connect one with PUT /email/mailbox."
)


def find_mailbox(db: Session, *, user_id: uuid.UUID) -> UserMailbox | None:
    """This user's mailbox row, or None. Never raises, never falls back."""

    return db.execute(
        select(UserMailbox).where(UserMailbox.user_id == user_id)
    ).scalar_one_or_none()


def _fallback_address() -> str | None:
    """The environment mailbox, only where it has been deliberately allowed.

    Off by default. The setting exists so an existing single-user deployment
    keeps working across the upgrade, not so that a multi-user one quietly
    shares an inbox.
    """

    if not settings.EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX:
        return None

    return (settings.EMAIL_MAILBOX_ADDRESS or "").strip() or None


def resolve_address(db: Session, *, user_id: uuid.UUID) -> str | None:
    """The address this user's mail operations act on, or None."""

    mailbox = find_mailbox(db, user_id=user_id)

    if mailbox is not None:
        return mailbox.address

    return _fallback_address()


def set_mailbox(
    db: Session,
    *,
    user_id: uuid.UUID,
    address: str,
    provider: str | None = None,
    display_name: str | None = None,
) -> UserMailbox:
    """Connect or change this user's mailbox.

    Validates the address here rather than at the provider, because the
    provider would only find out at the first call and the person would then be
    told their mailbox is unreachable when it is actually mistyped.

    The `user_id` argument is the *authenticated* user — routes pass
    `CurrentUser.id` and never a value from the request body.
    """

    parsed = parse_address(address)

    if parsed is None or not parsed.address:
        raise EmailValidationError(
            f"{address!r} is not a mailbox address. Give the address of the "
            "mailbox to act on, for example person@example.com."
        )

    if "@" not in parsed.address or parsed.address.startswith("@"):
        raise EmailValidationError(f"{address!r} is not a mailbox address.")

    name = (provider or configured_provider_name() or "").strip().lower()

    if not name:
        raise EmailProviderNotConfiguredError(
            "No email provider is configured on this server, so a mailbox "
            "cannot be connected. Set EMAIL_PROVIDER. Supported: "
            + ", ".join(supported_provider_names())
            + "."
        )

    if name not in supported_provider_names():
        raise EmailValidationError(
            f"{name!r} is not a provider this build knows. Supported: "
            + ", ".join(supported_provider_names())
            + "."
        )

    mailbox = find_mailbox(db, user_id=user_id)

    if mailbox is None:
        mailbox = UserMailbox(user_id=user_id)
        db.add(mailbox)

    mailbox.provider = name
    mailbox.address = parsed.address
    # A display name supplied explicitly wins; otherwise one parsed out of
    # "Name <addr>" is kept, because the caller clearly had one.
    mailbox.display_name = (display_name or parsed.name or None) or None

    db.commit()
    db.refresh(mailbox)

    logger.info(
        "user_mailbox_configured",
        extra={"user_id": str(user_id), "provider": name},
    )

    return mailbox


def disconnect_mailbox(db: Session, *, user_id: uuid.UUID) -> bool:
    """Remove this user's mailbox. True if there was one to remove."""

    mailbox = find_mailbox(db, user_id=user_id)

    if mailbox is None:
        return False

    db.delete(mailbox)
    db.commit()

    logger.info("user_mailbox_disconnected", extra={"user_id": str(user_id)})

    return True


def provider_for(db: Session, *, user_id: uuid.UUID) -> EmailProvider:
    """The provider bound to this user's mailbox, or refuse.

    The replacement for `get_provider()` everywhere a user is in scope. It
    raises rather than returning a provider pointed at nothing, so no caller
    can proceed and then have to invent what would have happened.
    """

    mailbox = find_mailbox(db, user_id=user_id)

    if mailbox is not None:
        return get_provider(mailbox=mailbox.address, provider=mailbox.provider)

    address = _fallback_address()

    if address is None:
        raise EmailProviderNotConfiguredError(NO_MAILBOX_DETAIL)

    return get_provider(mailbox=address)


def status_of(db: Session, *, user_id: uuid.UUID) -> ProviderStatus:
    """Describe this user's mailbox connection without ever raising.

    Called on every load of the email workspace, so it must always answer.
    """

    mailbox = find_mailbox(db, user_id=user_id)

    if mailbox is not None:
        return status_for(mailbox=mailbox.address, provider=mailbox.provider)

    address = _fallback_address()

    if address is None:
        return ProviderStatus(
            provider=configured_provider_name(),
            configured=False,
            connected=False,
            detail=NO_MAILBOX_DETAIL,
        )

    return status_for(mailbox=address)
