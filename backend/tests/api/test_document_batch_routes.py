"""Uploading several documents in one request.

The property under test throughout is isolation: one file's problem is that
file's problem. A batch that fails as a unit is worse than no batch endpoint at
all, because the caller cannot tell which of its files landed.
"""

from fastapi.testclient import TestClient

from tests.fixtures.factories import build_docx, build_markdown, build_pdf, build_text

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MARKDOWN_TYPE = "text/markdown"
PDF_TYPE = "application/pdf"
TEXT_TYPE = "text/plain"

ENDPOINT = "/documents/batch-upload"


def _file(name: str, data: bytes, content_type: str) -> tuple[str, tuple]:
    return ("files", (name, data, content_type))


def _by_name(body: dict) -> dict[str, dict]:
    return {item["filename"]: item for item in body["items"]}


# --- the happy path ------------------------------------------------------


def test_several_documents_are_ingested_in_one_request(client: TestClient) -> None:
    response = client.post(
        ENDPOINT,
        files=[
            _file("a.txt", build_text("The first document."), TEXT_TYPE),
            _file(
                "b.md", build_markdown([("H", "The second document.")]), MARKDOWN_TYPE
            ),
            _file("c.pdf", build_pdf(["The third document."]), PDF_TYPE),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert all(item["status"] == "indexed" for item in body["items"])


def test_each_result_identifies_its_own_file(client: TestClient) -> None:
    """A caller has to be able to match a result back to what it sent."""

    response = client.post(
        ENDPOINT,
        files=[
            _file("first.txt", build_text("One."), TEXT_TYPE),
            _file("second.txt", build_text("Two."), TEXT_TYPE),
        ],
    )

    items = _by_name(response.json())

    assert set(items) == {"first.txt", "second.txt"}
    assert items["first.txt"]["document_id"] != items["second.txt"]["document_id"]


def test_batch_uploaded_documents_appear_in_the_document_list(
    client: TestClient,
) -> None:
    """The batch endpoint is a way into the same knowledge base, not a
    parallel store."""

    client.post(
        ENDPOINT,
        files=[
            _file("a.txt", build_text("One."), TEXT_TYPE),
            _file("b.txt", build_text("Two."), TEXT_TYPE),
        ],
    )

    listed = client.get("/documents").json()

    assert listed["total"] == 2
    assert {item["filename"] for item in listed["items"]} == {"a.txt", "b.txt"}


def test_a_single_file_batch_is_allowed(client: TestClient) -> None:
    response = client.post(
        ENDPOINT, files=[_file("a.txt", build_text("One."), TEXT_TYPE)]
    )

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1


# --- isolation -----------------------------------------------------------


def test_a_failure_does_not_take_down_the_rest_of_the_batch(
    client: TestClient,
) -> None:
    """The example from the brief, end to end."""

    response = client.post(
        ENDPOINT,
        files=[
            _file("a.pdf", build_pdf(["Readable."]), PDF_TYPE),
            _file("b.docx", build_docx([("H", "Also readable.")]), DOCX_TYPE),
            _file("c.pdf", b"not a pdf at all", PDF_TYPE),
            _file("d.xlsx", b"spreadsheet bytes", "application/vnd.ms-excel"),
            _file("e.txt", build_text("Readable too."), TEXT_TYPE),
        ],
    )

    assert response.status_code == 200
    items = _by_name(response.json())

    assert items["a.pdf"]["result"] == "indexed"
    assert items["b.docx"]["result"] == "indexed"
    assert items["c.pdf"]["result"] == "failed"
    assert items["d.xlsx"]["result"] == "unsupported"
    assert items["e.txt"]["result"] == "indexed"

    assert response.json()["succeeded"] == 3
    assert response.json()["failed"] == 2


def test_the_files_after_a_failure_are_still_stored(client: TestClient) -> None:
    """Not just reported as successful — actually in the knowledge base. A
    poisoned session would report success and persist nothing."""

    client.post(
        ENDPOINT,
        files=[
            _file("broken.pdf", b"not a pdf at all", PDF_TYPE),
            _file("good.txt", build_text("Readable."), TEXT_TYPE),
        ],
    )

    listed = client.get("/documents", params={"status": "indexed"}).json()

    assert [item["filename"] for item in listed["items"]] == ["good.txt"]


def test_an_unsupported_file_reports_why(client: TestClient) -> None:
    response = client.post(
        ENDPOINT, files=[_file("legacy.doc", b"binary", "application/msword")]
    )

    item = response.json()["items"][0]

    assert item["result"] == "unsupported"
    assert item["succeeded"] is False
    assert item["document_id"] is None
    assert "Unsupported document type" in item["reason"]


def test_a_parser_failure_reports_a_document_to_inspect(client: TestClient) -> None:
    """Unlike an unsupported file, a parse failure has a row — the user should
    be able to look it up and see the error."""

    response = client.post(
        ENDPOINT, files=[_file("broken.pdf", b"not a pdf at all", PDF_TYPE)]
    )

    item = response.json()["items"][0]

    assert item["result"] == "failed"
    assert item["status"] == "failed"
    assert item["document_id"] is not None

    fetched = client.get(f"/documents/{item['document_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["error_message"]


def test_an_empty_file_is_rejected_without_a_document(client: TestClient) -> None:
    response = client.post(ENDPOINT, files=[_file("empty.txt", b"", TEXT_TYPE)])

    item = response.json()["items"][0]

    assert item["result"] == "failed"
    assert item["document_id"] is None
    assert client.get("/documents").json()["total"] == 0


def test_no_reason_leaks_a_stack_trace(client: TestClient) -> None:
    """Error bodies name what went wrong, never where in the code it went."""

    response = client.post(
        ENDPOINT,
        files=[
            _file("broken.pdf", b"not a pdf at all", PDF_TYPE),
            _file("legacy.doc", b"binary", "application/msword"),
        ],
    )

    for item in response.json()["items"]:
        reason = item["reason"] or ""
        assert reason
        assert "Traceback" not in reason
        assert 'File "' not in reason
        assert "app/services" not in reason


# --- idempotency through the API ----------------------------------------


def test_re_uploading_a_batch_does_not_duplicate_it(client: TestClient) -> None:
    """A caller that retries after a timeout must not double the corpus."""

    files = [
        _file("a.txt", build_text("One."), TEXT_TYPE),
        _file("b.txt", build_text("Two."), TEXT_TYPE),
    ]

    client.post(ENDPOINT, files=files)
    second = client.post(ENDPOINT, files=files)

    assert second.json()["succeeded"] == 2
    assert all(item["result"] == "unchanged" for item in second.json()["items"])
    assert client.get("/documents").json()["total"] == 2


def test_a_file_already_uploaded_singly_is_reported_unchanged(
    client: TestClient,
) -> None:
    """Both endpoints write to one knowledge base through one service, so the
    batch has to recognise what the single upload already stored."""

    data = build_text("One.")

    client.post("/documents/upload", files={"file": ("a.txt", data, TEXT_TYPE)})
    response = client.post(ENDPOINT, files=[_file("a.txt", data, TEXT_TYPE)])

    assert response.json()["items"][0]["result"] == "unchanged"
    assert client.get("/documents").json()["total"] == 1


def test_two_files_with_the_same_name_in_one_batch_stay_separate(
    client: TestClient,
) -> None:
    response = client.post(
        ENDPOINT,
        files=[
            _file("report.txt", build_text("Report for client A."), TEXT_TYPE),
            _file("report.txt", build_text("Report for client B."), TEXT_TYPE),
        ],
    )

    ids = {item["document_id"] for item in response.json()["items"]}

    assert len(ids) == 2
    assert client.get("/documents").json()["total"] == 2


def test_the_same_file_twice_in_one_batch_is_stored_once(client: TestClient) -> None:
    data = build_text("One.")

    response = client.post(
        ENDPOINT,
        files=[_file("a.txt", data, TEXT_TYPE), _file("a.txt", data, TEXT_TYPE)],
    )

    results = [item["result"] for item in response.json()["items"]]

    assert results == ["indexed", "unchanged"]
    assert client.get("/documents").json()["total"] == 1


# --- limits --------------------------------------------------------------


def test_an_oversized_batch_is_rejected(client: TestClient) -> None:
    """Ingestion is synchronous, so an unbounded batch holds the request open
    for as long as it takes to process every file in it."""

    from app.config.settings import settings

    files = [
        _file(f"file-{index}.txt", build_text(f"Body {index}."), TEXT_TYPE)
        for index in range(settings.MAX_BATCH_UPLOAD_FILES + 1)
    ]

    response = client.post(ENDPOINT, files=files)

    assert response.status_code == 413
    assert response.json()["error"] == "BatchTooLargeError"
    assert client.get("/documents").json()["total"] == 0


def test_a_batch_at_the_limit_is_accepted(client: TestClient) -> None:
    from app.config.settings import settings

    files = [
        _file(f"file-{index}.txt", build_text(f"Body {index}."), TEXT_TYPE)
        for index in range(settings.MAX_BATCH_UPLOAD_FILES)
    ]

    response = client.post(ENDPOINT, files=files)

    assert response.status_code == 200
    assert response.json()["succeeded"] == settings.MAX_BATCH_UPLOAD_FILES


def test_a_request_with_no_files_is_rejected(client: TestClient) -> None:
    assert client.post(ENDPOINT).status_code == 422


# --- the multipart contract ----------------------------------------------
#
# Swagger UI renders the request form from the published schema alone, so the
# schema is worth asserting separately from the behaviour: an endpoint can
# accept files perfectly well over the wire while describing them as something
# a client cannot send.
#
# **This FastAPI emits OpenAPI 3.1**, where a binary payload is marked with
# `contentMediaType: application/octet-stream`. It is *not* marked with
# `format: binary` — that is the OpenAPI 3.0 spelling, which 3.1 dropped when
# it adopted JSON Schema 2020-12. Verified against the installed FastAPI
# 0.135.1 / Pydantic 2.12.5, where every valid way of declaring the parameter
# produces the `contentMediaType` form and none produces `format`.
#
# So if these tests ever fail on a `KeyError` for one marker, check which
# OpenAPI version the app is emitting before touching the endpoint: a downgrade
# to a 3.0-emitting FastAPI would legitimately publish `format: binary` here.


def _batch_body_schema(client: TestClient) -> tuple[dict, dict]:
    """Return the batch endpoint's request-body media type and resolved schema."""

    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/documents/batch-upload"]["post"]["requestBody"]

    assert list(body["content"]) == ["multipart/form-data"], (
        "a file upload must be multipart/form-data"
    )

    media = body["content"]["multipart/form-data"]
    resolved = media["schema"]

    if "$ref" in resolved:
        name = resolved["$ref"].rsplit("/", 1)[-1]
        resolved = schema["components"]["schemas"][name]

    return media, resolved


def test_the_request_body_is_multipart_form_data(client: TestClient) -> None:
    _batch_body_schema(client)


def test_files_are_declared_as_binary_uploads(client: TestClient) -> None:
    """An array of binary items, not an array of strings.

    The distinction is the whole point: both spell `type: string`, and only
    the content marker on the *items* says the elements are file payloads
    rather than text a caller types in.
    """

    _media, resolved = _batch_body_schema(client)
    files = resolved["properties"]["files"]

    assert files["type"] == "array"
    assert files["items"]["type"] == "string"
    assert files["items"]["contentMediaType"] == "application/octet-stream"


def test_the_files_field_is_required(client: TestClient) -> None:
    _media, resolved = _batch_body_schema(client)

    assert "files" in resolved.get("required", [])


def test_the_single_upload_declares_one_binary_file(client: TestClient) -> None:
    """The endpoint that was already correct, pinned so it stays that way.

    It is also the reference the batch endpoint is measured against: this one
    is known to render as a file picker, so whatever marker it carries is the
    marker that works, and both endpoints must carry the same one.
    """

    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/documents/upload"]["post"]["requestBody"]

    assert list(body["content"]) == ["multipart/form-data"]

    resolved = body["content"]["multipart/form-data"]["schema"]
    if "$ref" in resolved:
        name = resolved["$ref"].rsplit("/", 1)[-1]
        resolved = schema["components"]["schemas"][name]

    assert resolved["properties"]["file"]["type"] == "string"
    assert resolved["properties"]["file"]["contentMediaType"] == (
        "application/octet-stream"
    )


def test_several_files_are_accepted_under_one_field_name(client: TestClient) -> None:
    """What the schema promises, exercised over the wire: repeating the field
    is how a multipart client sends more than one file, and it must bind to
    the list rather than the last value winning."""

    response = client.post(
        ENDPOINT,
        files=[
            _file("a.txt", build_text("One."), TEXT_TYPE),
            _file("b.txt", build_text("Two."), TEXT_TYPE),
            _file("c.txt", build_text("Three."), TEXT_TYPE),
        ],
    )

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert {item["filename"] for item in response.json()["items"]} == {
        "a.txt",
        "b.txt",
        "c.txt",
    }


# --- the single-file endpoint is unchanged -------------------------------


def test_single_upload_still_returns_the_document_itself(client: TestClient) -> None:
    """The batch endpoint returns per-file results; the single one still
    returns a document, at 201, exactly as before."""

    response = client.post(
        "/documents/upload",
        files={"file": ("a.txt", build_text("One."), TEXT_TYPE)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "a.txt"
    assert body["status"] == "indexed"
    assert "result" not in body


def test_single_upload_still_rejects_an_unsupported_type_with_415(
    client: TestClient,
) -> None:
    response = client.post(
        "/documents/upload",
        files={"file": ("legacy.doc", b"binary", "application/msword")},
    )

    assert response.status_code == 415
    assert client.get("/documents").json()["total"] == 0


def test_the_document_response_exposes_its_identity(client: TestClient) -> None:
    """`content_hash` is what a client compares to decide whether to re-upload
    at all, so it has to be visible."""

    response = client.post(
        "/documents/upload",
        files={"file": ("a.txt", build_text("One."), TEXT_TYPE)},
    )
    body = response.json()

    assert len(body["content_hash"]) == 64
    assert body["source_uri"] is None
    assert body["source_version"] is None
