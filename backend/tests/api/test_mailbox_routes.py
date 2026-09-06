"""The mailbox endpoints, and the boundary between two people using them.

Service-level isolation is tested in
`tests/services/features/email/test_mailbox_config_service.py`. These tests ask
the harder question: can the *HTTP surface* be talked into crossing that
boundary — by supplying a user id in a body, by omitting identity, or by one
user's write landing on another user's row.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import User

A_MAILBOX = "robert.keenan@sunradia.com"
B_MAILBOX = "sudha.gummuluru@sunradia.com"


@pytest.fixture(autouse=True)
def _outlook_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", "outlook")
    monkeypatch.setattr(settings_module.settings, "EMAIL_MAILBOX_ADDRESS", None)
    monkeypatch.setattr(
        settings_module.settings, "EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX", False
    )


def _make_user(db: Session, *, name: str, email: str) -> User:
    user = User(name=name, email=email, role="Director")
    db.add(user)
    db.commit()

    return user


@pytest.fixture
def two_users(db_session: Session) -> tuple[dict, dict]:
    a = _make_user(db_session, name="Robert Keenan", email="a@example.com")
    b = _make_user(db_session, name="Sudha Gummuluru", email="b@example.com")

    return {"X-User-ID": str(a.id)}, {"X-User-ID": str(b.id)}


# --- identity ------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_every_mailbox_endpoint_refuses_an_anonymous_caller(
    client: TestClient, method: str
) -> None:
    # `request` rather than the per-verb helpers: TestClient.delete takes no
    # json argument, and the point here is that all three verbs behave alike.
    response = client.request(
        method.upper(), "/email/mailbox", json={"address": A_MAILBOX}
    )

    assert response.status_code == 401


def test_a_user_id_in_the_body_is_ignored(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    """Identity comes from the header, and there is no second source of it.

    A body field naming another user must not move the mailbox onto them. The
    request is accepted — the extra key is simply not part of the contract —
    and the row it writes belongs to the authenticated caller.
    """

    a_headers, b_headers = two_users
    b_id = b_headers["X-User-ID"]

    client.put(
        "/email/mailbox",
        json={"address": A_MAILBOX, "user_id": b_id},
        headers=a_headers,
    )

    # B is untouched by a request that named B in its body.
    assert client.get("/email/mailbox", headers=b_headers).json()["connected"] is False


# --- isolation -----------------------------------------------------------


def test_two_users_see_only_their_own_mailbox(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a_headers, b_headers = two_users

    client.put("/email/mailbox", json={"address": A_MAILBOX}, headers=a_headers)
    client.put("/email/mailbox", json={"address": B_MAILBOX}, headers=b_headers)

    assert (
        client.get("/email/mailbox", headers=a_headers).json()["address"] == A_MAILBOX
    )
    assert (
        client.get("/email/mailbox", headers=b_headers).json()["address"] == B_MAILBOX
    )


def test_one_user_disconnecting_does_not_disconnect_the_other(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a_headers, b_headers = two_users

    client.put("/email/mailbox", json={"address": A_MAILBOX}, headers=a_headers)
    client.put("/email/mailbox", json={"address": B_MAILBOX}, headers=b_headers)

    assert client.delete("/email/mailbox", headers=a_headers).status_code == 200

    assert client.get("/email/mailbox", headers=a_headers).json()["connected"] is False
    assert (
        client.get("/email/mailbox", headers=b_headers).json()["address"] == B_MAILBOX
    )


# --- behaviour -----------------------------------------------------------


def test_a_user_with_no_mailbox_is_reported_as_not_connected(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a_headers, _ = two_users
    body = client.get("/email/mailbox", headers=a_headers).json()

    assert body["connected"] is False
    assert body["address"] is None
    assert body["detail"]


def test_connecting_then_reading_round_trips(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a_headers, _ = two_users

    created = client.put(
        "/email/mailbox",
        json={"address": A_MAILBOX, "display_name": "Robert"},
        headers=a_headers,
    )

    assert created.status_code == 200
    assert created.json()["connected"] is True
    assert created.json()["provider"] == "outlook"

    read = client.get("/email/mailbox", headers=a_headers).json()

    assert read["address"] == A_MAILBOX
    assert read["display_name"] == "Robert"
    assert read["shared_fallback"] is False


def test_disconnecting_is_idempotent(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a_headers, _ = two_users

    assert client.delete("/email/mailbox", headers=a_headers).status_code == 200
    assert client.delete("/email/mailbox", headers=a_headers).status_code == 200


def test_a_malformed_address_is_refused_with_422(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a_headers, _ = two_users
    response = client.put(
        "/email/mailbox", json={"address": "not-an-address"}, headers=a_headers
    )

    assert response.status_code == 422


def test_a_display_string_is_stored_as_an_address(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    """The Robert Keenan case must not be storable as a mailbox address."""

    a_headers, _ = two_users
    body = client.put(
        "/email/mailbox",
        json={"address": "Robert Keenan <Robert.Keenan@sunradia.com>"},
        headers=a_headers,
    ).json()

    assert body["address"] == "Robert.Keenan@sunradia.com"
    assert body["display_name"] == "Robert Keenan"
