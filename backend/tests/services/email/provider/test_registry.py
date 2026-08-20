"""The provider registry: what happens when there is no mailbox.

The most important assertion in this file is a negative one —
`test_no_provider_is_returned_when_none_is_configured`. If the registry ever
grows a fallback that accepts a send, every "Sent" badge in the product becomes
indistinguishable from a real one.
"""

import pytest

from app.core.exceptions import EmailProviderNotConfiguredError
from app.services.email.provider import registry


@pytest.fixture(autouse=True)
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from "no mailbox", whatever the developer's `.env`
    happens to hold. Without this the suite would pass or fail depending on
    whose machine it ran on."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", None)
    monkeypatch.setattr(settings_module.settings, "EMAIL_MAILBOX_ADDRESS", None)


def test_no_provider_is_returned_when_none_is_configured() -> None:
    with pytest.raises(EmailProviderNotConfiguredError):
        registry.get_provider()


def test_status_reports_not_configured_without_raising() -> None:
    """A status endpoint has to answer. This is what lets the UI show a setup
    notice instead of an error page on every load."""

    state = registry.status()

    assert state.configured is False
    assert state.connected is False
    assert state.provider is None
    assert "No mailbox is connected" in (state.detail or "")


def test_an_unknown_provider_name_is_named_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "carrier-pigeon")

    with pytest.raises(EmailProviderNotConfiguredError) as error:
        registry.get_provider()

    assert "carrier-pigeon" in str(error.value)
    assert "outlook" in str(error.value)


def test_the_provider_name_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "  Outlook  ")

    assert registry.configured_provider_name() == "outlook"


def test_a_blank_provider_name_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "   ")

    assert registry.configured_provider_name() is None


def test_outlook_without_a_mailbox_address_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application permissions carry no user, so every Graph mail call has to
    name a mailbox. Discovering that at send time would be the worst moment."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "outlook")
    monkeypatch.setattr(settings_module.settings, "EMAIL_MAILBOX_ADDRESS", None)

    with pytest.raises(EmailProviderNotConfiguredError) as error:
        registry.get_provider()

    assert "EMAIL_MAILBOX_ADDRESS" in str(error.value)


def test_outlook_without_graph_credentials_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph credentials are shared with OneDrive sync, so the missing-variable
    message names ONEDRIVE_*. It has to explain that rather than looking like a
    sync error that wandered into an email endpoint."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "outlook")
    monkeypatch.setattr(
        settings_module.settings, "EMAIL_MAILBOX_ADDRESS", "shared@sunradia.com"
    )
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_TENANT_ID", None)
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_ID", None)
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_SECRET", None)

    with pytest.raises(EmailProviderNotConfiguredError) as error:
        registry.get_provider()

    message = str(error.value)

    assert "ONEDRIVE_TENANT_ID" in message
    assert "OneDrive synchronisation" in message


def test_status_never_leaks_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "outlook")
    monkeypatch.setattr(
        settings_module.settings, "EMAIL_MAILBOX_ADDRESS", "shared@sunradia.com"
    )
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_TENANT_ID", "tenant")
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_ID", "client")
    monkeypatch.setattr(
        settings_module.settings, "ONEDRIVE_CLIENT_SECRET", "super-secret-value"
    )

    state = registry.status()

    assert "super-secret-value" not in (state.detail or "")
