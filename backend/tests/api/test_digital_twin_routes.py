"""The HTTP surface of the Digital Twin: one profile, many memories."""

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


# --- profile --------------------------------------------------------------


def test_reading_a_profile_before_one_exists_returns_404(client: TestClient) -> None:
    """An absent profile is not an empty one, and a client that cannot tell
    them apart shows blank fields as though someone had entered them."""

    response = client.get("/profile")

    assert response.status_code == 404
    assert response.json()["error"] == "ProfileNotFoundError"


def test_a_profile_can_be_written(client: TestClient) -> None:
    response = client.put("/profile", json=EXECUTIVE)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Test Executive"
    assert body["priorities"] == ["Government opportunities", "Enterprise AI"]
    uuid.UUID(body["id"])


def test_a_written_profile_can_be_read_back(client: TestClient) -> None:
    client.put("/profile", json=EXECUTIVE)

    body = client.get("/profile").json()

    assert body["role"] == "CEO"
    assert body["organization"] == "SunRadia"
    assert body["communication_style"] == "Concise and executive-friendly"
    assert body["decision_preferences"][0] == "Prefer evidence-backed recommendations"


def test_writing_again_updates_rather_than_duplicating(client: TestClient) -> None:
    client.put("/profile", json=EXECUTIVE)
    first = client.get("/profile").json()["id"]

    client.put("/profile", json={"role": "Chief Executive Officer"})
    body = client.get("/profile").json()

    assert body["id"] == first
    assert body["role"] == "Chief Executive Officer"
    assert body["name"] == "Test Executive"


def test_a_first_write_without_a_name_is_rejected(client: TestClient) -> None:
    response = client.put("/profile", json={"role": "CEO"})

    assert response.status_code == 422
    assert response.json()["error"] == "ProfileIncompleteError"


def test_a_blank_name_is_rejected(client: TestClient) -> None:
    response = client.put("/profile", json={**EXECUTIVE, "name": "   "})

    assert response.status_code == 422


def test_an_over_long_role_is_rejected(client: TestClient) -> None:
    response = client.put("/profile", json={**EXECUTIVE, "role": "x" * 300})

    assert response.status_code == 422


# --- memory ---------------------------------------------------------------


def test_a_memory_can_be_created(client: TestClient) -> None:
    response = client.post(
        "/memory",
        json={
            "type": "preference",
            "content": "Prefers concise executive-facing emails.",
            "importance": 4,
            "source": "user",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "preference"
    assert body["active"] is True
    uuid.UUID(body["id"])


def test_an_unknown_memory_type_is_rejected(client: TestClient) -> None:
    """The type decides how a memory is presented to the model, so a free
    string would quietly become a new category nobody handles."""

    response = client.post("/memory", json={"type": "hunch", "content": "x"})

    assert response.status_code == 422


def test_a_blank_memory_is_rejected(client: TestClient) -> None:
    response = client.post("/memory", json={"type": "fact", "content": "   "})

    assert response.status_code == 422


def test_an_out_of_range_importance_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/memory", json={"type": "fact", "content": "x", "importance": 9}
    )

    assert response.status_code == 422


def test_memories_are_listed_most_important_first(client: TestClient) -> None:
    client.post("/memory", json={"type": "fact", "content": "low", "importance": 1})
    client.post("/memory", json={"type": "fact", "content": "high", "importance": 5})

    body = client.get("/memory").json()

    assert body["total"] == 2
    assert [item["content"] for item in body["items"]] == ["high", "low"]


def test_memories_can_be_filtered_by_type(client: TestClient) -> None:
    client.post("/memory", json={"type": "fact", "content": "a fact"})
    client.post("/memory", json={"type": "decision", "content": "a decision"})

    body = client.get("/memory", params={"type": "decision"}).json()

    assert [item["content"] for item in body["items"]] == ["a decision"]


def test_memories_can_be_filtered_by_active(client: TestClient) -> None:
    created = client.post("/memory", json={"type": "fact", "content": "a fact"}).json()
    client.patch(f"/memory/{created['id']}", json={"active": False})

    assert client.get("/memory", params={"active": True}).json()["total"] == 0
    assert client.get("/memory", params={"active": False}).json()["total"] == 1


def test_a_memory_can_be_updated(client: TestClient) -> None:
    created = client.post("/memory", json={"type": "fact", "content": "old"}).json()

    body = client.patch(
        f"/memory/{created['id']}", json={"content": "new", "importance": 5}
    ).json()

    assert body["content"] == "new"
    assert body["importance"] == 5
    assert body["type"] == "fact"


def test_updating_an_unknown_memory_returns_404(client: TestClient) -> None:
    response = client.patch(f"/memory/{uuid.uuid4()}", json={"content": "x"})

    assert response.status_code == 404
    assert response.json()["error"] == "MemoryNotFoundError"


def test_a_memory_can_be_deleted(client: TestClient) -> None:
    created = client.post("/memory", json={"type": "fact", "content": "x"}).json()

    assert client.delete(f"/memory/{created['id']}").status_code == 204
    assert client.get("/memory").json()["total"] == 0


def test_deleting_an_unknown_memory_returns_404(client: TestClient) -> None:
    assert client.delete(f"/memory/{uuid.uuid4()}").status_code == 404


# --- chat is unchanged from the caller's side -----------------------------


def test_chat_still_takes_the_same_request(
    client: TestClient, db_session, embed_query_as, fake_llm
) -> None:
    """Adding a Digital Twin must not change the chat contract."""

    from app.models.document import Document, DocumentChunk, DocumentStatus

    embed_query_as([1.0, 0.0])
    fake_llm("An answer.")
    client.put("/profile", json=EXECUTIVE)
    client.post(
        "/memory",
        json={"type": "decision", "content": "Prioritize government opportunities."},
    )

    document = Document(
        user_id="mvp-user",
        filename="capabilities.pdf",
        content_type="application/pdf",
        file_size_bytes=64,
        status=DocumentStatus.INDEXED,
        chunk_count=1,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentChunk(
            document_id=document.id,
            user_id="mvp-user",
            chunk_index=0,
            content="Sun Radia delivers data modernization.",
            char_count=38,
            page_number=2,
            embedding=[1.0, 0.0],
        )
    )
    db_session.commit()

    response = client.post("/chat", json={"question": "What do we do?", "top_k": 5})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "retrieved_chunks"}
    assert body["sources"][0]["document"] == "capabilities.pdf"
