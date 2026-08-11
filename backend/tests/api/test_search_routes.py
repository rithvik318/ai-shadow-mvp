"""`POST /search` is retrieval with the model taken out.

The assertions that matter most here are the negative ones: that no completion
is requested, and that the endpoint answers "nothing" rather than erroring when
there is nothing. Everything else it returns is retrieval's, and is asserted
against in `tests/services/features/retrieval/`.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import EmbeddingError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.llm.embedding_service import embedding_service

# Two-dimensional vectors, so every expected ordering can be read off by eye.
# Against the query EAST: cosine similarity 1.0, ~0.707, 0.0 and -1.0.
EAST = [1.0, 0.0]
NORTH_EAST = [1.0, 1.0]
NORTH = [0.0, 1.0]
WEST = [-1.0, 0.0]


def _seed(
    db: Session,
    chunks: list[tuple[str, list[float] | None]],
    *,
    filename: str = "handbook.pdf",
    status: DocumentStatus = DocumentStatus.INDEXED,
    user_id: str = "mvp-user",
    page_number: int | None = 12,
    section_title: str | None = None,
) -> Document:
    """Persist a document and chunks with hand-chosen vectors."""

    document = Document(
        user_id=user_id,
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
            user_id=user_id,
            chunk_index=index,
            content=content,
            char_count=len(content),
            page_number=page_number,
            section_title=section_title,
            embedding=vector,
        )
        for index, (content, vector) in enumerate(chunks)
    )
    db.commit()

    return document


# --- what a search returns ------------------------------------------------


def test_search_returns_the_matching_passages(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    document = _seed(
        db_session,
        [("Holiday accrues monthly.", EAST)],
        section_title="Leave",
    )

    response = client.post("/search", json={"question": "How does holiday accrue?"})

    assert response.status_code == 200
    body = response.json()
    assert body["retrieved_count"] == 1
    assert body["results"] == [
        {
            "document": "handbook.pdf",
            "section": "Leave",
            "page": 12,
            "similarity": pytest.approx(1.0),
            "content": "Holiday accrues monthly.",
            "document_id": str(document.id),
            "chunk_id": body["results"][0]["chunk_id"],
        }
    ]
    uuid.UUID(body["results"][0]["chunk_id"])


def test_the_passage_text_comes_back_in_full(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    """Without the text, a similarity score cannot explain itself — which is
    the whole reason this endpoint exists rather than reading `/chat` sources."""

    passage = "A" * 1500
    embed_query_as(EAST)
    _seed(db_session, [(passage, EAST)])

    body = client.post("/search", json={"question": "q?"}).json()

    assert body["results"][0]["content"] == passage


def test_results_are_ordered_most_similar_first(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("far", NORTH), ("near", EAST), ("middling", NORTH_EAST)])

    body = client.post("/search", json={"question": "q?"}).json()

    assert [result["content"] for result in body["results"]] == [
        "near",
        "middling",
        "far",
    ]
    similarities = [result["similarity"] for result in body["results"]]
    assert similarities == sorted(similarities, reverse=True)


def test_the_query_is_echoed_back(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    """Echoed as searched, not as typed: the scores belong to the trimmed text
    that was actually embedded."""

    embed_query_as(EAST)
    _seed(db_session, [("body", EAST)])

    body = client.post("/search", json={"question": "  holiday policy  "}).json()

    assert body["query"] == "holiday policy"


def test_retrieved_count_matches_the_results(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("a", EAST), ("b", NORTH_EAST)])

    body = client.post("/search", json={"question": "q?"}).json()

    assert body["retrieved_count"] == len(body["results"]) == 2


# --- the model is not involved -------------------------------------------


def test_no_completion_is_requested(
    client: TestClient, db_session: Session, embed_query_as, fake_llm
) -> None:
    """The point of the endpoint: retrieval without the model, so relevance can
    be judged without paying for — or being persuaded by — an answer."""

    embed_query_as(EAST)
    calls = fake_llm("should not be used")
    _seed(db_session, [("Holiday accrues monthly.", EAST)])

    response = client.post("/search", json={"question": "holiday?"})

    assert response.status_code == 200
    assert response.json()["retrieved_count"] == 1
    assert calls == []


def test_no_answer_field_is_returned(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    """A client must not be able to mistake this for `/chat` output."""

    embed_query_as(EAST)
    _seed(db_session, [("body", EAST)])

    body = client.post("/search", json={"question": "q?"}).json()

    assert set(body) == {"query", "results", "retrieved_count"}


# --- what is eligible to be found ----------------------------------------


def test_an_empty_knowledge_base_returns_200_with_no_results(
    client: TestClient, embed_query_as
) -> None:
    """Nothing to search is a result, not a failure."""

    embed_query_as(EAST)

    response = client.post("/search", json={"question": "q?"})

    assert response.status_code == 200
    assert response.json() == {"query": "q?", "results": [], "retrieved_count": 0}


def test_a_passage_below_the_similarity_floor_is_not_returned(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    """The configured floor applies here exactly as it does to chat — this
    endpoint reports what retrieval does, it does not relax it."""

    embed_query_as(EAST)
    _seed(db_session, [("opposite", WEST)])

    body = client.post("/search", json={"question": "q?"}).json()

    assert body["retrieved_count"] == 0


def test_documents_still_indexing_are_not_searched(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("perfect match", EAST)], status=DocumentStatus.PROCESSING)

    body = client.post("/search", json={"question": "q?"}).json()

    assert body["retrieved_count"] == 0


def test_results_are_scoped_to_the_owning_user(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("mine", EAST)], filename="mine.pdf", user_id="mvp-user")
    _seed(db_session, [("theirs", EAST)], filename="theirs.pdf", user_id="someone-else")

    body = client.post("/search", json={"question": "q?"}).json()

    assert [result["document"] for result in body["results"]] == ["mine.pdf"]


def test_a_repeated_passage_takes_only_one_place(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    """The corpus holds the same document under several filenames; two copies
    of one passage must not fill two of the places a caller asked for."""

    embed_query_as(EAST)
    _seed(db_session, [("Shared boilerplate.", EAST)], filename="v1.docx")
    _seed(db_session, [("shared   BOILERPLATE.", NORTH_EAST)], filename="v2.docx")

    body = client.post("/search", json={"question": "q?"}).json()

    assert body["retrieved_count"] == 1
    assert body["results"][0]["content"] == "Shared boilerplate."


# --- provenance -----------------------------------------------------------


def test_a_docx_passage_is_located_by_its_heading(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    """DOCX has no page without rendering the file, so the heading is the only
    locator it has."""

    embed_query_as(EAST)
    _seed(
        db_session,
        [("body", EAST)],
        filename="policy.docx",
        page_number=None,
        section_title="Data Governance",
    )

    result = client.post("/search", json={"question": "q?"}).json()["results"][0]

    assert result["section"] == "Data Governance"
    assert result["page"] is None


def test_a_pptx_passage_carries_its_slide_ordinal(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("Slide body.", EAST)], filename="deck.pptx", page_number=7)

    result = client.post("/search", json={"question": "q?"}).json()["results"][0]

    assert result["page"] == 7


def test_a_text_passage_may_have_neither_locator(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("body", EAST)], filename="notes.txt", page_number=None)

    result = client.post("/search", json={"question": "q?"}).json()["results"][0]

    assert result["page"] is None
    assert result["section"] is None


# --- request validation ---------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_a_blank_question_is_rejected_with_422(
    client: TestClient, question: str
) -> None:
    assert client.post("/search", json={"question": question}).status_code == 422


def test_a_missing_question_is_rejected_with_422(client: TestClient) -> None:
    assert client.post("/search", json={}).status_code == 422


@pytest.mark.parametrize("top_k", [0, -1, 51])
def test_an_out_of_range_top_k_is_rejected_with_422(
    client: TestClient, top_k: int
) -> None:
    """The same 1–50 window `/chat` enforces; search does not get its own."""

    response = client.post("/search", json={"question": "q?", "top_k": top_k})

    assert response.status_code == 422


def test_top_k_limits_the_results_returned(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("a", EAST), ("b", NORTH_EAST), ("c", NORTH)])

    body = client.post("/search", json={"question": "q?", "top_k": 2}).json()

    assert body["retrieved_count"] == 2
    assert len(body["results"]) == 2


def test_top_k_may_be_omitted(
    client: TestClient, db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("body", EAST)])

    assert client.post("/search", json={"question": "q?"}).status_code == 200


# --- provider failure -----------------------------------------------------


def test_an_embedding_failure_returns_502(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider outage must not be reported as "nothing found" — that would
    send someone tuning a threshold against a broken pipeline."""

    _seed(db_session, [("body", EAST)])

    def failing(text: str) -> list[float]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_query", failing)

    response = client.post("/search", json={"question": "q?"})

    assert response.status_code == 502
    assert response.json()["error"] == "EmbeddingError"


# --- end to end -----------------------------------------------------------


def test_an_uploaded_document_can_be_searched(
    client: TestClient, embed_query_as
) -> None:
    """Upload, index, embed, retrieve — the whole pipeline minus the model."""

    upload = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"Holiday accrues monthly.", "text/plain")},
    )
    assert upload.status_code == 201

    # Pinned after ingestion so the query vector matches the stored one, which
    # ingestion derived from the chunk text.
    from tests.support.embeddings import deterministic_vector

    embed_query_as(deterministic_vector("Holiday accrues monthly."))

    body = client.post("/search", json={"question": "How does holiday accrue?"}).json()

    assert body["retrieved_count"] == 1
    assert body["results"][0]["document"] == "notes.txt"
    assert body["results"][0]["content"] == "Holiday accrues monthly."
    uuid.UUID(body["results"][0]["document_id"])
