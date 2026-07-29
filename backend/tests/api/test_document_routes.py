import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import EmbeddingError
from app.models.document import DocumentChunk, DocumentStatus
from app.services.llm.embedding_service import embedding_service
from tests.fixtures.factories import build_docx, build_markdown, build_pdf

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload(client: TestClient, data: bytes, name: str, content_type: str):
    return client.post("/documents/upload", files={"file": (name, data, content_type)})


# --- POST /documents/upload ---------------------------------------------


def test_upload_pdf_returns_201_with_indexed_document(client: TestClient) -> None:
    """A successful upload returns the fully ingested document."""

    response = _upload(
        client, build_pdf(["Page one", "Page two"]), "report.pdf", PDF_TYPE
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "report.pdf"
    assert body["status"] == DocumentStatus.INDEXED.value
    assert body["page_count"] == 2
    assert body["chunk_count"] > 0
    assert body["error_message"] is None
    uuid.UUID(body["id"])


def test_upload_docx_is_accepted(client: TestClient) -> None:
    response = _upload(
        client, build_docx([("Intro", "Body text.")]), "paper.docx", DOCX_TYPE
    )

    assert response.status_code == 201
    assert response.json()["status"] == DocumentStatus.INDEXED.value


def test_upload_markdown_is_accepted(client: TestClient) -> None:
    response = _upload(
        client, build_markdown([("Overview", "Body.")]), "notes.md", "text/markdown"
    )

    assert response.status_code == 201


def test_upload_plain_text_is_accepted(client: TestClient) -> None:
    response = _upload(client, b"Some plain text body.", "notes.txt", "text/plain")

    assert response.status_code == 201


def test_upload_persists_chunks(client: TestClient, db_session: Session) -> None:
    """The chunks written by ingestion are visible in the database."""

    response = _upload(client, b"Some plain text body.", "notes.txt", "text/plain")
    document_id = uuid.UUID(response.json()["id"])

    chunks = (
        db_session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        .scalars()
        .all()
    )

    assert len(chunks) == response.json()["chunk_count"]


def test_upload_rejects_unsupported_type_with_415(client: TestClient) -> None:
    response = _upload(client, b"PK\x03\x04", "archive.zip", "application/zip")

    assert response.status_code == 415
    assert response.json()["error"] == "UnsupportedDocumentTypeError"


def test_upload_rejects_empty_file_with_422(client: TestClient) -> None:
    response = _upload(client, b"", "notes.txt", "text/plain")

    assert response.status_code == 422
    assert response.json()["error"] == "EmptyDocumentError"


def test_upload_rejects_oversized_file_with_413(
    client: TestClient, monkeypatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "MAX_UPLOAD_SIZE_BYTES", 10)

    response = _upload(client, b"x" * 50, "notes.txt", "text/plain")

    assert response.status_code == 413
    assert response.json()["error"] == "DocumentTooLargeError"


def test_upload_rejects_corrupt_pdf_with_422(client: TestClient) -> None:
    response = _upload(client, b"not a pdf", "broken.pdf", PDF_TYPE)

    assert response.status_code == 422
    assert response.json()["error"] == "DocumentParseError"


def test_upload_without_a_file_returns_422(client: TestClient) -> None:
    """FastAPI's own request validation rejects a missing file part."""

    assert client.post("/documents/upload").status_code == 422


def test_upload_returns_502_when_the_embedding_provider_fails(
    client: TestClient, monkeypatch
) -> None:
    """A provider outage is an upstream failure, not the client's fault."""

    def failing(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_texts", failing)

    response = _upload(client, b"Some plain text body.", "notes.txt", "text/plain")

    assert response.status_code == 502
    assert response.json()["error"] == "EmbeddingError"


def test_a_document_that_failed_to_embed_is_visible_as_failed(
    client: TestClient, monkeypatch
) -> None:
    """The user can see why, rather than the upload vanishing."""

    def failing(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_texts", failing)
    _upload(client, b"Some plain text body.", "notes.txt", "text/plain")

    failed = client.get("/documents", params={"status": "failed"}).json()

    assert failed["total"] == 1
    assert failed["items"][0]["error_message"]


# --- GET /documents ------------------------------------------------------


def test_list_documents_returns_empty_page_initially(client: TestClient) -> None:
    response = client.get("/documents")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_documents_returns_uploaded_documents(client: TestClient) -> None:
    _upload(client, b"first body", "one.txt", "text/plain")
    _upload(client, b"second body", "two.txt", "text/plain")

    body = client.get("/documents").json()

    assert body["total"] == 2
    assert {item["filename"] for item in body["items"]} == {"one.txt", "two.txt"}


def test_list_documents_honours_limit_and_offset(client: TestClient) -> None:
    for index in range(3):
        _upload(client, f"body {index}".encode(), f"doc{index}.txt", "text/plain")

    body = client.get("/documents", params={"limit": 2, "offset": 0}).json()

    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2


def test_list_documents_filters_by_status(client: TestClient) -> None:
    _upload(client, b"good body", "ok.txt", "text/plain")
    _upload(client, b"not a pdf", "broken.pdf", PDF_TYPE)

    indexed = client.get("/documents", params={"status": "indexed"}).json()
    failed = client.get("/documents", params={"status": "failed"}).json()

    assert indexed["total"] == 1
    assert indexed["items"][0]["filename"] == "ok.txt"
    assert failed["total"] == 1
    assert failed["items"][0]["filename"] == "broken.pdf"


def test_list_documents_rejects_an_invalid_limit(client: TestClient) -> None:
    assert client.get("/documents", params={"limit": 0}).status_code == 422
    assert client.get("/documents", params={"limit": 500}).status_code == 422


# --- GET /documents/{id} -------------------------------------------------


def test_get_document_returns_the_document(client: TestClient) -> None:
    created = _upload(client, b"body text", "notes.txt", "text/plain").json()

    response = client.get(f"/documents/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_document_exposes_the_failure_reason(client: TestClient) -> None:
    """A failed ingest is retrievable, with its error explained."""

    created = _upload(client, b"not a pdf", "broken.pdf", PDF_TYPE)
    assert created.status_code == 422

    listed = client.get("/documents", params={"status": "failed"}).json()
    document = client.get(f"/documents/{listed['items'][0]['id']}").json()

    assert document["status"] == DocumentStatus.FAILED.value
    assert document["error_message"]


def test_get_unknown_document_returns_404(client: TestClient) -> None:
    response = client.get(f"/documents/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"] == "DocumentNotFoundError"


def test_get_document_rejects_a_malformed_id(client: TestClient) -> None:
    assert client.get("/documents/not-a-uuid").status_code == 422


# --- DELETE /documents/{id} ---------------------------------------------


def test_delete_document_returns_204_and_removes_it(client: TestClient) -> None:
    created = _upload(client, b"body text", "notes.txt", "text/plain").json()

    assert client.delete(f"/documents/{created['id']}").status_code == 204
    assert client.get(f"/documents/{created['id']}").status_code == 404


def test_delete_document_removes_its_chunks(
    client: TestClient, db_session: Session
) -> None:
    created = _upload(client, b"body text", "notes.txt", "text/plain").json()

    client.delete(f"/documents/{created['id']}")

    assert db_session.execute(select(DocumentChunk)).scalars().all() == []


def test_delete_unknown_document_returns_404(client: TestClient) -> None:
    assert client.delete(f"/documents/{uuid.uuid4()}").status_code == 404


# --- health --------------------------------------------------------------


def test_health_endpoint(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "healthy"}


def test_root_endpoint_reports_the_service(client: TestClient) -> None:
    assert client.get("/").json()["status"] == "running"
