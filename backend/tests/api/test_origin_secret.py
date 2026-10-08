"""The proxy-origin check: with `ORIGIN_SECRET` set, only the proxy gets in.

`/users` stands in for every API route — it is the one that lists the ids
`X-User-ID` trusts, so it is exactly what must not be reachable directly.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config.settings import settings
from app.main import ORIGIN_SECRET_HEADER

SECRET = "s3cret-value-for-tests-only"


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ORIGIN_SECRET", SecretStr(SECRET))


def test_the_correct_secret_is_accepted(client: TestClient, guarded: None) -> None:
    response = client.get("/users", headers={ORIGIN_SECRET_HEADER: SECRET})

    assert response.status_code == 200


def test_a_missing_secret_is_rejected(client: TestClient, guarded: None) -> None:
    response = client.get("/users")

    assert response.status_code == 403
    assert response.json()["error"] == "OriginNotAllowed"


def test_an_incorrect_secret_is_rejected(client: TestClient, guarded: None) -> None:
    response = client.get("/users", headers={ORIGIN_SECRET_HEADER: SECRET + "x"})

    assert response.status_code == 403


def test_health_needs_no_secret(client: TestClient, guarded: None) -> None:
    """The platform's health check calls the service directly."""

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_no_secret_configured_means_no_check(client: TestClient) -> None:
    """Local development, where nothing sits in front of the API."""

    assert settings.ORIGIN_SECRET is None
    assert client.get("/users").status_code == 200


def test_a_rejection_never_logs_either_value(
    client: TestClient, guarded: None, caplog: pytest.LogCaptureFixture
) -> None:
    supplied = "attacker-guess-value"

    with caplog.at_level(logging.DEBUG):
        client.get("/users", headers={ORIGIN_SECRET_HEADER: supplied})

    assert SECRET not in caplog.text
    assert supplied not in caplog.text
    assert "origin_secret_rejected" in caplog.text
