import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import EmbeddingError, LLMServiceError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.chat.chat_service import NO_CONTEXT_ANSWER
from app.services.llm.embedding_service import embedding_service
from app.services.llm.llm_service import llm_service

EAST = [1.0, 0.0]
NORTH_EAST = [1.0, 1.0]
NORTH = [0.0, 1.0]


def _seed(
    db: Session,
    chunks: list[tuple[str, list[float]]],
    *,
    filename: str = "handbook.pdf",
    status: DocumentStatus = DocumentStatus.INDEXED,
    page_number: int | None = 12,
) -> Document:
    document = Document(
        user_id="mvp-user",
        filename=filename,
        content_type="application/pdf",
        file_size_bytes=64,
        status=status,
        chunk_count=len(chunks),
    )
    db.add(document)
    db.flush()
    db.add_all(
        DocumentChunk(
            document_id=document.id,
            user_id="mvp-user",
            chunk_index=index,
            content=content,
            char_count=len(content),
            page_number=page_number,
            embedding=vector,
        )
        for index, (content, vector) in enumerate(chunks)
    )
    db.commit()
    return document


# --- POST /chat ----------------------------------------------------------


def test_ask_returns_an_answer_with_sources(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("Holiday accrues at two days a month.")
    document = _seed(db_session, [("Holiday accrues monthly.", EAST)])

    response = client.post("/chat", json={"question": "How does holiday accrue?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Holiday accrues at two days a month."
    assert body["retrieved_chunks"] == 1
    assert body["sources"] == [
        {
            "document_id": str(document.id),
            "filename": "handbook.pdf",
            "page_number": 12,
            "similarity": pytest.approx(1.0),
        }
    ]


def test_sources_are_ordered_most_similar_first(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("An answer.")
    _seed(db_session, [("far", NORTH), ("near", EAST), ("middling", NORTH_EAST)])

    body = client.post("/chat", json={"question": "q?"}).json()

    similarities = [source["similarity"] for source in body["sources"]]
    assert similarities == sorted(similarities, reverse=True)


def test_top_k_limits_the_sources_returned(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("An answer.")
    _seed(db_session, [("a", EAST), ("b", NORTH_EAST), ("c", NORTH)])

    body = client.post("/chat", json={"question": "q?", "top_k": 2}).json()

    assert body["retrieved_chunks"] == 2
    assert len(body["sources"]) == 2


def test_an_empty_knowledge_base_returns_200_with_no_sources(
    client: TestClient, embed_query_as, fake_llm
) -> None:
    """Nothing to answer from is an answer, not a failure — and a client can
    tell it apart from a real answer by `retrieved_chunks`."""

    embed_query_as(EAST)
    calls = fake_llm("should not be used")

    response = client.post("/chat", json={"question": "q?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == NO_CONTEXT_ANSWER
    assert body["sources"] == []
    assert body["retrieved_chunks"] == 0
    assert calls == []


def test_documents_still_indexing_are_not_answered_from(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("should not be used")
    _seed(db_session, [("perfect match", EAST)], status=DocumentStatus.PROCESSING)

    body = client.post("/chat", json={"question": "q?"}).json()

    assert body["retrieved_chunks"] == 0


def test_an_uploaded_document_can_be_asked_about(
    client: TestClient, embed_query_as, fake_llm
) -> None:
    """End to end across all three PRs: upload, index, embed, retrieve, answer."""

    upload = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"Holiday accrues monthly.", "text/plain")},
    )
    assert upload.status_code == 201

    # Pinned after ingestion so the query vector matches the stored one, which
    # ingestion derived from the chunk text.
    from tests.support.embeddings import deterministic_vector

    embed_query_as(deterministic_vector("Holiday accrues monthly."))
    fake_llm("Two days a month.")

    body = client.post("/chat", json={"question": "How does holiday accrue?"}).json()

    assert body["answer"] == "Two days a month."
    assert body["retrieved_chunks"] == 1
    assert body["sources"][0]["filename"] == "notes.txt"
    uuid.UUID(body["sources"][0]["document_id"])


# --- request validation --------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_a_blank_question_is_rejected_with_422(
    client: TestClient, question: str
) -> None:
    assert client.post("/chat", json={"question": question}).status_code == 422


def test_a_missing_question_is_rejected_with_422(client: TestClient) -> None:
    assert client.post("/chat", json={}).status_code == 422


@pytest.mark.parametrize("top_k", [0, -1, 51])
def test_an_out_of_range_top_k_is_rejected_with_422(
    client: TestClient, top_k: int
) -> None:
    response = client.post("/chat", json={"question": "q?", "top_k": top_k})

    assert response.status_code == 422


def test_top_k_may_be_omitted(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("An answer.")
    _seed(db_session, [("body", EAST)])

    assert client.post("/chat", json={"question": "q?"}).status_code == 200


# --- provider failures ---------------------------------------------------


def test_an_llm_failure_returns_502(
    client: TestClient,
    db_session: Session,
    embed_query_as,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model outage is an upstream failure, not "nothing found"."""

    embed_query_as(EAST)
    _seed(db_session, [("body", EAST)])

    def failing(messages: list) -> str:
        raise LLMServiceError("model down")

    monkeypatch.setattr(llm_service, "complete", failing)

    response = client.post("/chat", json={"question": "q?"})

    assert response.status_code == 502
    assert response.json()["error"] == "LLMServiceError"


def test_an_embedding_failure_returns_502(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db_session, [("body", EAST)])

    def failing(text: str) -> list[float]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_query", failing)

    response = client.post("/chat", json={"question": "q?"})

    assert response.status_code == 502
    assert response.json()["error"] == "EmbeddingError"


def test_the_endpoint_is_stateless(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    """No history is kept: the second call is shown exactly what the first was."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed(db_session, [("body", EAST)])

    client.post("/chat", json={"question": "first question"})
    client.post("/chat", json={"question": "second question"})

    assert len(calls) == 2
    assert len(calls[0]) == len(calls[1]) == 2
    assert "first question" not in calls[1][1]["content"]
