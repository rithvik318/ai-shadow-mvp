"""Deleting a user over HTTP: who may, who may not, and what survives.

`is_admin` is the only authorisation check in the API, and this is the only
endpoint it guards. These tests pin both halves of that: an ordinary user is
refused, and the refusal is a 403 rather than a 401 — repeating the request
with the same identity will never work, so a login prompt would be the wrong
advice.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.models.document import Document, DocumentStatus
from app.models.user import User


@pytest.fixture
def admin_headers(db_session: Session) -> dict[str, str]:
    admin = User(
        name="Administrator",
        email="admin@example.com",
        role="Operations",
        is_admin=True,
    )
    db_session.add(admin)
    db_session.commit()

    return {"X-User-ID": str(admin.id)}


def _document(db: Session, filename: str) -> None:
    db.add(
        Document(
            user_id=MVP_USER_ID,
            filename=filename,
            content_type="text/plain",
            file_size_bytes=10,
            status=DocumentStatus.INDEXED,
            content_hash=uuid.uuid4().hex,
        )
    )
    db.commit()


# --- authorisation -------------------------------------------------------


def test_an_anonymous_caller_is_refused(client: TestClient, test_user: User) -> None:
    assert client.delete(f"/users/{test_user.id}").status_code == 401


def test_an_ordinary_user_cannot_delete_anybody(
    client: TestClient, user_headers: dict, test_user_b: User
) -> None:
    response = client.delete(f"/users/{test_user_b.id}", headers=user_headers)

    assert response.status_code == 403
    assert "administrator" in response.json()["detail"].lower()


def test_an_ordinary_user_cannot_delete_themselves_either(
    client: TestClient, user_headers: dict, test_user: User
) -> None:
    """Self-deletion is still an administrative act in this design.

    Deleting an account destroys a Digital Twin and every draft attached to it;
    it is not the same class of operation as editing a preference.
    """

    assert (
        client.delete(f"/users/{test_user.id}", headers=user_headers).status_code == 403
    )


def test_a_role_string_of_admin_grants_nothing(
    client: TestClient, db_session: Session, test_user_b: User
) -> None:
    """`role` is a persona label, not permission. Typing "Admin" must not work."""

    impostor = User(name="Impostor", email="impostor@example.com", role="Admin")
    db_session.add(impostor)
    db_session.commit()

    response = client.delete(
        f"/users/{test_user_b.id}", headers={"X-User-ID": str(impostor.id)}
    )

    assert response.status_code == 403


def test_an_administrator_may_delete(
    client: TestClient, admin_headers: dict, test_user: User
) -> None:
    response = client.delete(f"/users/{test_user.id}", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["deleted"]["users"] == 1


def test_deleting_somebody_who_does_not_exist_is_a_404(
    client: TestClient, admin_headers: dict
) -> None:
    assert (
        client.delete(f"/users/{uuid.uuid4()}", headers=admin_headers).status_code
        == 404
    )


# --- the confirmation flow -----------------------------------------------


def test_the_preview_reports_what_would_be_destroyed(
    client: TestClient, admin_headers: dict, test_user: User
) -> None:
    """The dialog's numbers come from the server, not from a hard-coded list."""

    body = client.get(
        f"/users/{test_user.id}/deletion-preview", headers=admin_headers
    ).json()

    assert body["user_id"] == str(test_user.id)
    assert "task" in body["owned"]
    assert "digital_twin_memory" in body["owned"]
    assert "email_draft" in body["owned"]


def test_the_preview_destroys_nothing(
    client: TestClient, admin_headers: dict, test_user: User
) -> None:
    client.get(f"/users/{test_user.id}/deletion-preview", headers=admin_headers)

    assert client.get("/users", headers=admin_headers).json()["total"] == 2


def test_the_preview_needs_administrator_rights_too(
    client: TestClient, user_headers: dict, test_user_b: User
) -> None:
    """Counting somebody's private data is itself a disclosure."""

    assert (
        client.get(
            f"/users/{test_user_b.id}/deletion-preview", headers=user_headers
        ).status_code
        == 403
    )


def test_the_preview_states_that_shared_knowledge_is_safe(
    client: TestClient, admin_headers: dict, test_user: User, db_session: Session
) -> None:
    _document(db_session, "capabilities.pdf")

    body = client.get(
        f"/users/{test_user.id}/deletion-preview", headers=admin_headers
    ).json()

    assert body["shared_knowledge_documents"] == 1
    assert "not owned by this person" in body["shared_knowledge_note"]


# --- what survives -------------------------------------------------------


def test_the_shared_knowledge_base_survives_a_deletion(
    client: TestClient, admin_headers: dict, test_user: User, db_session: Session
) -> None:
    """The guarantee the milestone asked to be proved, at the API boundary."""

    _document(db_session, "capabilities.pdf")
    _document(db_session, "case-study.pdf")

    body = client.delete(f"/users/{test_user.id}", headers=admin_headers).json()

    assert body["shared_knowledge_documents"] == 2
    assert db_session.query(Document).count() == 2


def test_deleting_one_user_leaves_the_others_listed(
    client: TestClient, admin_headers: dict, test_user: User, test_user_b: User
) -> None:
    client.delete(f"/users/{test_user.id}", headers=admin_headers)

    remaining = {
        item["email"]
        for item in client.get("/users", headers=admin_headers).json()["items"]
    }

    assert test_user.email not in remaining
    assert test_user_b.email in remaining
