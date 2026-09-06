"""Choosing the mailbox provider, and being honest when there is none.

Two functions, and the difference between them is the whole point. `status_for`
answers "is this mailbox reachable?" and never raises, so a UI can render a
setup notice instead of an error. `get_provider()` answers "give me the
mailbox" and raises when there is not one, so no caller can accidentally
proceed without a provider and then have to invent what would have happened.

**Which mailbox is an argument, not a global.** `get_provider(mailbox=...)` is
how a per-user mailbox reaches a provider — see
`services/features/email/mailbox_config_service.py`, which owns the question of
whose mailbox that is. Omitting it falls back to `EMAIL_MAILBOX_ADDRESS`, which
exists only for single-user and development deployments; this module does not
decide when that is acceptable, because that decision needs a user and this
layer deliberately has none.

**There is no null provider, no in-memory provider and no demo provider.** A
provider that accepts a send and returns success without a mailbox would make
every "Sent" badge in the product a lie, and the lie would be indistinguishable
from the truth. Not configured is an error at the point of sending, and a
disabled button in the UI. That is the entire fallback.
"""

import logging

from app.config.settings import settings
from app.core.exceptions import EmailProviderNotConfiguredError
from app.services.email.provider.base import EmailProvider, ProviderStatus
from app.services.email.provider.outlook_provider import OutlookEmailProvider

logger = logging.getLogger(__name__)

# The one place a provider name is turned into an implementation. Adding Gmail
# is an entry here plus a module beside `outlook_provider.py`; nothing above
# this module changes, which is the claim the whole boundary exists to make.
_BUILDERS = {
    "outlook": OutlookEmailProvider.from_settings,
}

NOT_CONFIGURED_DETAIL = (
    "No mailbox is connected. Drafting, rewriting, templates and saved drafts "
    "all work without one; listing an inbox and sending do not. Set "
    "EMAIL_PROVIDER on the server, then connect a mailbox with "
    "PUT /email/mailbox."
)


def supported_provider_names() -> list[str]:
    """Every provider name this build can construct."""

    return sorted(_BUILDERS)


def configured_provider_name() -> str | None:
    """The configured provider name, normalised, or None."""

    name = (settings.EMAIL_PROVIDER or "").strip().lower()

    return name or None


def get_provider(
    *, mailbox: str | None = None, provider: str | None = None
) -> EmailProvider:
    """The provider for a mailbox, or refuse.

    Constructed per call rather than cached. The construction is cheap — the
    expensive thing is the Graph token, and `GraphClient` caches that itself —
    and a module-level instance would hold configuration read at import time,
    which is exactly the pattern `app/services/llm/client.py` exists to avoid.

    Caching would also be wrong now for a second reason: one process serves
    many users with different mailboxes, and a cached provider would be bound
    to whichever mailbox asked first.
    """

    name = (provider or "").strip().lower() or configured_provider_name()

    if name is None:
        raise EmailProviderNotConfiguredError(NOT_CONFIGURED_DETAIL)

    builder = _BUILDERS.get(name)

    if builder is None:
        raise EmailProviderNotConfiguredError(
            f"EMAIL_PROVIDER is set to {name!r}, which is not a provider this "
            "build knows. Supported: " + ", ".join(supported_provider_names()) + "."
        )

    return builder(mailbox=mailbox)


def status_for(
    *, mailbox: str | None = None, provider: str | None = None
) -> ProviderStatus:
    """Describe a mailbox connection without raising, ever.

    Called on every load of the email workspace, so a failure here must not be
    able to take the page down — hence the broad catch. What it returns is
    always a describable state, never an exception.
    """

    name = (provider or "").strip().lower() or configured_provider_name()

    if name is None:
        return ProviderStatus(
            provider=None,
            configured=False,
            connected=False,
            detail=NOT_CONFIGURED_DETAIL,
        )

    try:
        return get_provider(mailbox=mailbox, provider=name).status()
    except EmailProviderNotConfiguredError as exc:
        return ProviderStatus(
            provider=name, configured=False, connected=False, detail=str(exc)
        )
    except Exception:  # noqa: BLE001 - a status endpoint must always answer
        logger.exception("email_provider_status_failed", extra={"provider": name})

        return ProviderStatus(
            provider=name,
            configured=True,
            connected=False,
            mailbox=mailbox,
            detail=(
                "The mailbox provider could not be reached. The server log has "
                "the details."
            ),
        )


def status() -> ProviderStatus:
    """The deployment-level connection state, with no user in scope.

    Retained for callers that legitimately have no user — server diagnostics.
    Anything acting for a person asks
    `mailbox_config_service.status_of(db, user_id=...)` instead.
    """

    return status_for()
