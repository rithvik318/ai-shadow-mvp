"""The HTTP surface of the Digital Twin: one profile per user, many memories.

Every request here carries `X-User-ID`, the MVP identity header. The tests at
the end are the ones that matter most: they assert that two users cannot reach
each other's twin through this API at all.
"""

import uuid

from fastapi.testclient import TestClient

EXECUTIVE = {
    "name": "Test Executive",
    "role": "CEO",
    "organization": "SunRadia",
    "communication_style": "Concise and executive-friendly",
    "responsibilities": ["Business development", "Strategic partnerships"],
    "expertise": ["Data modernization", "Analytics"],
    "priorities": ["Government opportunities", "Enterprise AI"],
    "decision_preferences": [
        "Prefer evidence-backed recommendations",
        "Avoid unnecessary detail",
    ],
    "current_focus": ["Freddie Mac analytics discussion"],
}

MANAGER = {
    "name": "Test CRM Manager",
    "role": "CRM Manager",
    "organization": "SunRadia",
    "communication_style": "Operational and detailed",
    "priorities": ["Pipeline management"],
    "current_focus": ["Customer follow-ups"],
}

Headers = dict[str, str]


# --- identity -------------------------------------------------------------


def test_a_request_without_identity_is_refused(client: TestClient) -> None:
    """These endpoints act on behalf of somebody. Answering without knowing
    who would mean picking a twin, and picking is exactly what must not
    happen."""

    response = client.get("/profile")

    assert response.status_code == 401
    assert response.json()["error"] == "MissingIdentityError"


def test_an_identity_that_is_not_a_uuid_is_refused(client: TestClient) -> None:
    response = client.get("/profile", headers={"X-User-ID": "not-a-uuid"})

    assert response.status_code == 422
    assert response.json()["error"] == "MalformedIdentityError"


def test_an_unknown_identity_is_refused(client: TestClient) -> None:
    """A user is never created on first use: an identity nobody chose is one
    typo away from a private twin of its own."""

    response = client.get("/profile", headers={"X-User-ID": str(uuid.uuid4())})

    assert response.status_code == 404
    assert response.json()["error"] == "UserNotFoundError"


def test_an_empty_identity_header_is_refused(client: TestClient) -> None:
    response = client.get("/profile", headers={"X-User-ID": "   "})

    assert response.status_code == 401


def test_memory_also_requires_identity(client: TestClient) -> None:
    assert client.get("/memory").status_code == 401
    assert (
        client.post("/memory", json={"type": "fact", "content": "x"}).status_code == 401
    )


def test_chat_also_requires_identity(client: TestClient) -> None:
    assert client.post("/chat", json={"question": "q?"}).status_code == 401


# --- profile --------------------------------------------------------------


def test_reading_a_profile_before_one_exists_returns_404(
    client: TestClient, user_headers: Headers
) -> None:
    """An absent profile is not an empty one, and a client that cannot tell
    them apart shows blank fields as though someone had entered them."""

    response = client.get("/profile", headers=user_headers)

    assert response.status_code == 404
    assert response.json()["error"] == "ProfileNotFoundError"


def test_a_profile_can_be_written(client: TestClient, user_headers: Headers) -> None:
    response = client.put("/profile", json=EXECUTIVE, headers=user_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Test Executive"
    assert body["priorities"] == ["Government opportunities", "Enterprise AI"]
    uuid.UUID(body["id"])


def test_a_written_profile_can_be_read_back(
    client: TestClient, user_headers: Headers
) -> None:
    client.put("/profile", json=EXECUTIVE, headers=user_headers)

    body = client.get("/profile", headers=user_headers).json()

    assert body["role"] == "CEO"
    assert body["organization"] == "SunRadia"
    assert body["communication_style"] == "Concise and executive-friendly"
    assert body["decision_preferences"][0] == "Prefer evidence-backed recommendations"


def test_writing_again_updates_rather_than_duplicating(
    client: TestClient, user_headers: Headers
) -> None:
    client.put("/profile", json=EXECUTIVE, headers=user_headers)
    first = client.get("/profile", headers=user_headers).json()["id"]

    client.put(
        "/profile", json={"role": "Chief Executive Officer"}, headers=user_headers
    )
    body = client.get("/profile", headers=user_headers).json()

    assert body["id"] == first
    assert body["role"] == "Chief Executive Officer"
    assert body["name"] == "Test Executive"


def test_a_first_write_without_a_name_is_rejected(
    client: TestClient, user_headers: Headers
) -> None:
    response = client.put("/profile", json={"role": "CEO"}, headers=user_headers)

    assert response.status_code == 422
    assert response.json()["error"] == "ProfileIncompleteError"


def test_a_blank_name_is_rejected(client: TestClient, user_headers: Headers) -> None:
    response = client.put(
        "/profile", json={**EXECUTIVE, "name": "   "}, headers=user_headers
    )

    assert response.status_code == 422


def test_an_over_long_role_is_rejected(
    client: TestClient, user_headers: Headers
) -> None:
    response = client.put(
        "/profile", json={**EXECUTIVE, "role": "x" * 300}, headers=user_headers
    )

    assert response.status_code == 422


def test_a_body_cannot_choose_whose_profile_is_written(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    """The owner comes from the header and nowhere else. A body that names
    another user is not an error — the field simply does not exist, and the
    write lands where the header said."""

    client.put(
        "/profile",
        json={**EXECUTIVE, "user_id": user_b_headers["X-User-ID"]},
        headers=user_headers,
    )

    assert client.get("/profile", headers=user_headers).json()["name"] == (
        "Test Executive"
    )
    assert client.get("/profile", headers=user_b_headers).status_code == 404


# --- memory ---------------------------------------------------------------


def test_a_memory_can_be_created(client: TestClient, user_headers: Headers) -> None:
    response = client.post(
        "/memory",
        json={
            "type": "preference",
            "content": "Prefers concise executive-facing emails.",
            "importance": 4,
            "source": "user",
        },
        headers=user_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "preference"
    assert body["active"] is True
    uuid.UUID(body["id"])


def test_an_unknown_memory_type_is_rejected(
    client: TestClient, user_headers: Headers
) -> None:
    """The type decides how a memory is presented to the model, so a free
    string would quietly become a new category nobody handles."""

    response = client.post(
        "/memory", json={"type": "hunch", "content": "x"}, headers=user_headers
    )

    assert response.status_code == 422


def test_a_blank_memory_is_rejected(client: TestClient, user_headers: Headers) -> None:
    response = client.post(
        "/memory", json={"type": "fact", "content": "   "}, headers=user_headers
    )

    assert response.status_code == 422


def test_an_out_of_range_importance_is_rejected(
    client: TestClient, user_headers: Headers
) -> None:
    response = client.post(
        "/memory",
        json={"type": "fact", "content": "x", "importance": 9},
        headers=user_headers,
    )

    assert response.status_code == 422


def test_memories_are_listed_most_important_first(
    client: TestClient, user_headers: Headers
) -> None:
    client.post(
        "/memory",
        json={"type": "fact", "content": "low", "importance": 1},
        headers=user_headers,
    )
    client.post(
        "/memory",
        json={"type": "fact", "content": "high", "importance": 5},
        headers=user_headers,
    )

    body = client.get("/memory", headers=user_headers).json()

    assert body["total"] == 2
    assert [item["content"] for item in body["items"]] == ["high", "low"]


def test_memories_can_be_filtered_by_type(
    client: TestClient, user_headers: Headers
) -> None:
    client.post(
        "/memory", json={"type": "fact", "content": "a fact"}, headers=user_headers
    )
    client.post(
        "/memory",
        json={"type": "decision", "content": "a decision"},
        headers=user_headers,
    )

    body = client.get(
        "/memory", params={"type": "decision"}, headers=user_headers
    ).json()

    assert [item["content"] for item in body["items"]] == ["a decision"]


def test_memories_can_be_filtered_by_active(
    client: TestClient, user_headers: Headers
) -> None:
    created = client.post(
        "/memory", json={"type": "fact", "content": "a fact"}, headers=user_headers
    ).json()
    client.patch(
        f"/memory/{created['id']}", json={"active": False}, headers=user_headers
    )

    active = client.get("/memory", params={"active": True}, headers=user_headers)
    retired = client.get("/memory", params={"active": False}, headers=user_headers)

    assert active.json()["total"] == 0
    assert retired.json()["total"] == 1


def test_a_memory_can_be_updated(client: TestClient, user_headers: Headers) -> None:
    created = client.post(
        "/memory", json={"type": "fact", "content": "old"}, headers=user_headers
    ).json()

    body = client.patch(
        f"/memory/{created['id']}",
        json={"content": "new", "importance": 5},
        headers=user_headers,
    ).json()

    assert body["content"] == "new"
    assert body["importance"] == 5
    assert body["type"] == "fact"


def test_updating_an_unknown_memory_returns_404(
    client: TestClient, user_headers: Headers
) -> None:
    response = client.patch(
        f"/memory/{uuid.uuid4()}", json={"content": "x"}, headers=user_headers
    )

    assert response.status_code == 404
    assert response.json()["error"] == "MemoryNotFoundError"


def test_a_memory_can_be_deleted(client: TestClient, user_headers: Headers) -> None:
    created = client.post(
        "/memory", json={"type": "fact", "content": "x"}, headers=user_headers
    ).json()

    deleted = client.delete(f"/memory/{created['id']}", headers=user_headers)

    assert deleted.status_code == 204
    assert client.get("/memory", headers=user_headers).json()["total"] == 0


def test_deleting_an_unknown_memory_returns_404(
    client: TestClient, user_headers: Headers
) -> None:
    response = client.delete(f"/memory/{uuid.uuid4()}", headers=user_headers)

    assert response.status_code == 404


# --- isolation between users ---------------------------------------------


def test_two_users_keep_separate_profiles(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    client.put("/profile", json=EXECUTIVE, headers=user_headers)
    client.put("/profile", json=MANAGER, headers=user_b_headers)

    assert client.get("/profile", headers=user_headers).json()["role"] == "CEO"
    assert (
        client.get("/profile", headers=user_b_headers).json()["role"] == "CRM Manager"
    )


def test_one_user_cannot_overwrite_anothers_profile(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    client.put("/profile", json=EXECUTIVE, headers=user_headers)

    client.put("/profile", json=MANAGER, headers=user_b_headers)

    assert client.get("/profile", headers=user_headers).json()["name"] == (
        "Test Executive"
    )


def test_a_user_without_a_profile_does_not_inherit_one(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    """404, not somebody else's profile. This is the failure that would be
    invisible in an answer and catastrophic in a draft email."""

    client.put("/profile", json=EXECUTIVE, headers=user_headers)

    assert client.get("/profile", headers=user_b_headers).status_code == 404


def test_each_user_sees_only_their_own_memories(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    client.post(
        "/memory",
        json={"type": "preference", "content": "Prefers concise summaries."},
        headers=user_headers,
    )
    client.post(
        "/memory",
        json={"type": "preference", "content": "Prefers detailed pipeline status."},
        headers=user_b_headers,
    )

    mine = client.get("/memory", headers=user_headers).json()
    theirs = client.get("/memory", headers=user_b_headers).json()

    assert [item["content"] for item in mine["items"]] == ["Prefers concise summaries."]
    assert [item["content"] for item in theirs["items"]] == [
        "Prefers detailed pipeline status."
    ]


def test_one_user_cannot_update_anothers_memory(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    """404 rather than 403: a refusal would confirm the memory exists, which
    is itself a fact about somebody else's Digital Twin."""

    theirs = client.post(
        "/memory", json={"type": "fact", "content": "theirs"}, headers=user_b_headers
    ).json()

    response = client.patch(
        f"/memory/{theirs['id']}", json={"content": "hijacked"}, headers=user_headers
    )

    assert response.status_code == 404
    unchanged = client.get("/memory", headers=user_b_headers).json()
    assert unchanged["items"][0]["content"] == "theirs"


def test_one_user_cannot_delete_anothers_memory(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    theirs = client.post(
        "/memory", json={"type": "fact", "content": "theirs"}, headers=user_b_headers
    ).json()

    response = client.delete(f"/memory/{theirs['id']}", headers=user_headers)

    assert response.status_code == 404
    assert client.get("/memory", headers=user_b_headers).json()["total"] == 1


def test_retiring_a_memory_does_not_affect_another_user(
    client: TestClient, user_headers: Headers, user_b_headers: Headers
) -> None:
    mine = client.post(
        "/memory", json={"type": "fact", "content": "mine"}, headers=user_headers
    ).json()
    client.post(
        "/memory", json={"type": "fact", "content": "theirs"}, headers=user_b_headers
    )

    client.patch(f"/memory/{mine['id']}", json={"active": False}, headers=user_headers)

    assert (
        client.get("/memory", params={"active": True}, headers=user_headers).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.get("/memory", params={"active": True}, headers=user_b_headers).json()[
            "total"
        ]
        == 1
    )
