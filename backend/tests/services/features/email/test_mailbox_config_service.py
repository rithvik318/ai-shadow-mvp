"""One mailbox per person, and no way to reach anybody else's.

These tests are the isolation guarantee written down. Every one of them would
have passed trivially under the old `EMAIL_MAILBOX_ADDRESS` design *because
there was only one mailbox* — which is exactly why they are worth having now
that there are many.
"""

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import EmailProviderNotConfiguredError, EmailValidationError
from app.models.user import User
from app.services.features.email import mailbox_config_service

A_MAILBOX = "robert.keenan@sunradia.com"
B_MAILBOX = "sudha.gummuluru@sunradia.com"


@pytest.fixture
def user_a(db_session: Session) -> User:
    user = User(name="Robert Keenan", email="a@example.com", role="Director")
    db_session.add(user)
    db_session.commit()

    return user


@pytest.fixture
def user_b(db_session: Session) -> User:
    user = User(name="Sudha Gummuluru", email="b@example.com", role="Principal")
    db_session.add(user)
    db_session.commit()

    return user


@pytest.fixture(autouse=True)
def _outlook_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider is configured, but no shared mailbox and no fallback."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "outlook")
    monkeypatch.setattr(settings_module.settings, "EMAIL_MAILBOX_ADDRESS", None)
    monkeypatch.setattr(
        settings_module.settings, "EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX", False
    )


# --- isolation -----------------------------------------------------------


def test_each_user_reads_only_their_own_mailbox(
    db_session: Session, user_a: User, user_b: User
) -> None:
    mailbox_config_service.set_mailbox(db_session, user_id=user_a.id, address=A_MAILBOX)
    mailbox_config_service.set_mailbox(db_session, user_id=user_b.id, address=B_MAILBOX)

    assert (
        mailbox_config_service.resolve_address(db_session, user_id=user_a.id)
        == A_MAILBOX
    )
    assert (
        mailbox_config_service.resolve_address(db_session, user_id=user_b.id)
        == B_MAILBOX
    )


def test_changing_one_users_mailbox_leaves_the_other_untouched(
    db_session: Session, user_a: User, user_b: User
) -> None:
    mailbox_config_service.set_mailbox(db_session, user_id=user_a.id, address=A_MAILBOX)
    mailbox_config_service.set_mailbox(db_session, user_id=user_b.id, address=B_MAILBOX)

    mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address="moved@sunradia.com"
    )

    assert (
        mailbox_config_service.resolve_address(db_session, user_id=user_b.id)
        == B_MAILBOX
    )


def test_disconnecting_one_user_leaves_the_other_connected(
    db_session: Session, user_a: User, user_b: User
) -> None:
    mailbox_config_service.set_mailbox(db_session, user_id=user_a.id, address=A_MAILBOX)
    mailbox_config_service.set_mailbox(db_session, user_id=user_b.id, address=B_MAILBOX)

    assert mailbox_config_service.disconnect_mailbox(db_session, user_id=user_a.id)

    assert mailbox_config_service.find_mailbox(db_session, user_id=user_a.id) is None
    assert (
        mailbox_config_service.resolve_address(db_session, user_id=user_b.id)
        == B_MAILBOX
    )


def test_a_user_with_no_mailbox_gets_nothing_not_somebody_elses(
    db_session: Session, user_a: User, user_b: User
) -> None:
    """The failure mode the whole design exists to prevent."""

    mailbox_config_service.set_mailbox(db_session, user_id=user_a.id, address=A_MAILBOX)

    assert mailbox_config_service.resolve_address(db_session, user_id=user_b.id) is None

    with pytest.raises(EmailProviderNotConfiguredError):
        mailbox_config_service.provider_for(db_session, user_id=user_b.id)


def test_one_mailbox_per_person_is_enforced_by_replacing_not_adding(
    db_session: Session, user_a: User
) -> None:
    first = mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address=A_MAILBOX
    )
    second = mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address="second@sunradia.com"
    )

    assert first.id == second.id
    assert second.address == "second@sunradia.com"


# --- the shared fallback -------------------------------------------------


def test_no_shared_mailbox_leaks_when_the_fallback_is_off(
    db_session: Session, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured EMAIL_MAILBOX_ADDRESS must not silently become everyone's.

    This is the regression that matters most in the whole milestone: the old
    behaviour handed every user the same inbox, and it looked like the feature
    working.
    """

    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "EMAIL_MAILBOX_ADDRESS", "shared@sunradia.com"
    )

    assert mailbox_config_service.resolve_address(db_session, user_id=user_a.id) is None

    status = mailbox_config_service.status_of(db_session, user_id=user_a.id)

    assert status.configured is False


def test_the_fallback_applies_only_when_switched_on(
    db_session: Session, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-user deployments keep working, but only by saying so."""

    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "EMAIL_MAILBOX_ADDRESS", "shared@sunradia.com"
    )
    monkeypatch.setattr(
        settings_module.settings, "EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX", True
    )

    assert (
        mailbox_config_service.resolve_address(db_session, user_id=user_a.id)
        == "shared@sunradia.com"
    )


def test_a_users_own_mailbox_always_beats_the_fallback(
    db_session: Session, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "EMAIL_MAILBOX_ADDRESS", "shared@sunradia.com"
    )
    monkeypatch.setattr(
        settings_module.settings, "EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX", True
    )
    mailbox_config_service.set_mailbox(db_session, user_id=user_a.id, address=A_MAILBOX)

    assert (
        mailbox_config_service.resolve_address(db_session, user_id=user_a.id)
        == A_MAILBOX
    )


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "first.last@example.com",
        "first.last+tag@example.com",
        "UPPERCASE@Example.COM",
        "someone@subdomain.example.co.uk",
    ],
)
def test_normal_addresses_are_accepted(
    db_session: Session, user_a: User, address: str
) -> None:
    mailbox = mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address=address
    )

    assert mailbox.address == address


def test_a_display_string_is_stored_as_its_address_not_whole(
    db_session: Session, user_a: User
) -> None:
    """The Robert Keenan case, at the mailbox boundary this time."""

    mailbox = mailbox_config_service.set_mailbox(
        db_session,
        user_id=user_a.id,
        address="Robert Keenan <Robert.Keenan@sunradia.com>",
    )

    assert mailbox.address == "Robert.Keenan@sunradia.com"
    assert mailbox.display_name == "Robert Keenan"


@pytest.mark.parametrize("address", ["not-an-address", "@example.com", "   "])
def test_a_malformed_address_is_refused(
    db_session: Session, user_a: User, address: str
) -> None:
    with pytest.raises(EmailValidationError):
        mailbox_config_service.set_mailbox(
            db_session, user_id=user_a.id, address=address
        )


def test_an_unknown_provider_is_refused(db_session: Session, user_a: User) -> None:
    with pytest.raises(EmailValidationError):
        mailbox_config_service.set_mailbox(
            db_session, user_id=user_a.id, address=A_MAILBOX, provider="carrier-pigeon"
        )


# --- the provider a send would actually use ------------------------------
#
# The tests above prove the *row* is scoped. These prove the object built from
# it is too — a correctly scoped lookup feeding a provider pointed somewhere
# else would be a leak with a clean audit trail.


@pytest.fixture
def outlook_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enough configuration for a provider to be constructible.

    Credentials are fictitious and no network call is made: the assertions are
    about which mailbox the provider was built for, which is decided before
    anything is sent.
    """

    from app.config import settings as settings_module

    for name, value in (
        ("EMAIL_PROVIDER", "outlook"),
        ("ONEDRIVE_TENANT_ID", "tenant-x"),
        ("ONEDRIVE_CLIENT_ID", "client-x"),
        ("ONEDRIVE_CLIENT_SECRET", "secret-x"),
        ("EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX", False),
        ("EMAIL_MAILBOX_ADDRESS", None),
    ):
        monkeypatch.setattr(settings_module.settings, name, value)


def test_each_user_gets_a_provider_bound_to_their_own_mailbox(
    db_session: Session, user_a: User, user_b: User, outlook_configured: None
) -> None:
    """The property the whole multi-user milestone rests on."""

    mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address="a@sunradia.com"
    )
    mailbox_config_service.set_mailbox(
        db_session, user_id=user_b.id, address="b@sunradia.com"
    )

    assert (
        mailbox_config_service.provider_for(db_session, user_id=user_a.id).mailbox
        == "a@sunradia.com"
    )
    assert (
        mailbox_config_service.provider_for(db_session, user_id=user_b.id).mailbox
        == "b@sunradia.com"
    )


def test_a_user_with_no_mailbox_is_refused_rather_than_given_anothers(
    db_session: Session, user_a: User, user_b: User, outlook_configured: None
) -> None:
    """The failure mode this replaced: one address in the environment meant
    every user acted as whoever was configured."""

    mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address="a@sunradia.com"
    )

    with pytest.raises(EmailProviderNotConfiguredError):
        mailbox_config_service.provider_for(db_session, user_id=user_b.id)


def test_changing_one_users_mailbox_does_not_repoint_anothers_provider(
    db_session: Session, user_a: User, user_b: User, outlook_configured: None
) -> None:
    """A provider is built per call and never cached across users, so a change
    to one person's mailbox cannot be observed through another's."""

    mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address="a@sunradia.com"
    )
    mailbox_config_service.set_mailbox(
        db_session, user_id=user_b.id, address="b@sunradia.com"
    )

    mailbox_config_service.set_mailbox(
        db_session, user_id=user_a.id, address="a2@sunradia.com"
    )

    assert (
        mailbox_config_service.provider_for(db_session, user_id=user_a.id).mailbox
        == "a2@sunradia.com"
    )
    assert (
        mailbox_config_service.provider_for(db_session, user_id=user_b.id).mailbox
        == "b@sunradia.com"
    )


def test_a_display_string_never_reaches_the_provider_as_an_address(
    db_session: Session, user_a: User, outlook_configured: None
) -> None:
    """Phase 2's defect, checked at the point it would actually do damage."""

    mailbox_config_service.set_mailbox(
        db_session,
        user_id=user_a.id,
        address="Robert Keenan <Robert.Keenan@sunradia.com>",
    )

    assert mailbox_config_service.provider_for(
        db_session, user_id=user_a.id
    ).mailbox == ("Robert.Keenan@sunradia.com")
