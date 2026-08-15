"""Creating the people the Shadow can answer for.

An identity API, not an account system: enough to make two users and get their
ids, and deliberately no more.
"""

import uuid

from fastapi.testclient import TestClient

CEO = {"name": "Test CEO", "email": "ceo@example.com", "role": "CEO"}
CRM = {"name": "Test CRM Manager", "email": "crm@example.com", "role": "CRM Manager"}


def test_a_user_can_be_created(client: TestClient) -> None:
    response = client.post("/users", json=CEO)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test CEO"
    assert body["role"] == "CEO"
    uuid.UUID(body["id"])


def test_a_duplicate_email_is_rejected(client: TestClient) -> None:
    """Email is the only human-readable handle on a row whose id is a UUID.
    Two users sharing one would be indistinguishable to whoever made them."""

    client.post("/users", json=CEO)

    response = client.post("/users", json={**CEO, "name": "Someone Else"})

    assert response.status_code == 409
    assert response.json()["error"] == "DuplicateUserError"


def test_email_case_does_not_create_a_second_user(client: TestClient) -> None:
    client.post("/users", json=CEO)

    response = client.post("/users", json={**CEO, "email": "CEO@Example.com"})

    assert response.status_code == 409


def test_users_can_be_listed(client: TestClient) -> None:
    client.post("/users", json=CEO)
    client.post("/users", json=CRM)

    body = client.get("/users").json()

    assert body["total"] == 2
    assert {item["role"] for item in body["items"]} == {"CEO", "CRM Manager"}


def test_the_list_is_empty_before_anyone_is_created(client: TestClient) -> None:
    body = client.get("/users").json()

    assert body == {"items": [], "total": 0}


def test_a_blank_name_is_rejected(client: TestClient) -> None:
    assert client.post("/users", json={**CEO, "name": "   "}).status_code == 422


def test_something_that_is_not_an_email_is_rejected(client: TestClient) -> None:
    assert client.post("/users", json={**CEO, "email": "nope"}).status_code == 422


def test_a_created_user_can_be_used_as_an_identity(client: TestClient) -> None:
    """The point of the endpoint: the id it returns is what `X-User-ID` takes."""

    created = client.post("/users", json=CEO).json()

    response = client.get("/profile", headers={"X-User-ID": created["id"]})

    # 404 because this user has no profile yet — but the identity resolved,
    # which is what distinguishes it from the 401 and 422 cases.
    assert response.status_code == 404
    assert response.json()["error"] == "ProfileNotFoundError"
