import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    DocumentParseError,
    DocumentTooLargeError,
    EmbeddingError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.documents.indexing_service import (
    backfill_missing_embeddings,
)
from app.services.features.documents.ingestion_service import (
    ingest_document,
    validate_upload,
)
from app.services.llm.embedding_service import embedding_service
from tests.fixtures.factories import build_markdown, build_pdf, build_text

PDF_TYPE = "application/pdf"


# --- validation ----------------------------------------------------------


def test_validate_upload_accepts_a_supported_file() -> None:
    """A well-formed upload passes validation silently."""

    validate_upload(build_text("body"), "notes.txt", "text/plain")


def test_validate_upload_rejects_empty_bytes() -> None:
    with pytest.raises(EmptyDocumentError):
        validate_upload(b"", "notes.txt", "text/plain")


def test_validate_upload_rejects_a_missing_filename() -> None:
    with pytest.raises(EmptyDocumentError):
        validate_upload(b"body", "   ", "text/plain")


def test_validate_upload_rejects_unsupported_types() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(b"body", "archive.zip", "application/zip")


def test_validate_upload_rejects_oversized_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The size limit comes from configuration."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "MAX_UPLOAD_SIZE_BYTES", 10)

    with pytest.raises(DocumentTooLargeError):
        validate_upload(b"x" * 11, "notes.txt", "text/plain")


def test_validate_upload_accepts_a_file_exactly_at_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The limit is inclusive."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "MAX_UPLOAD_SIZE_BYTES", 10)

    validate_upload(b"x" * 10, "notes.txt", "text/plain")


# --- successful ingestion ------------------------------------------------


def test_ingest_document_persists_document_and_chunks(db_session: Session) -> None:
    """A successful ingest stores the document and all of its chunks."""

    data = build_markdown([("Overview", "Body text of the overview section.")])

    document = ingest_document(
        db_session, data=data, filename="notes.md", content_type="text/markdown"
    )

    assert document.status == DocumentStatus.INDEXED
    assert document.chunk_count > 0
    assert document.file_size_bytes == len(data)
    assert document.error_message is None

    chunks = (
        db_session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        .scalars()
        .all()
    )
    assert len(chunks) == document.chunk_count


def test_ingest_document_records_page_count_for_pdfs(db_session: Session) -> None:
    """Page count is taken from the parser and stored on the document."""

    document = ingest_document(
        db_session,
        data=build_pdf(["Page one text", "Page two text"]),
        filename="report.pdf",
        content_type=PDF_TYPE,
    )

    assert document.page_count == 2


def test_ingested_chunks_carry_provenance(db_session: Session) -> None:
    """Chunks persist the page and heading their text came from."""

    document = ingest_document(
        db_session,
        data=build_pdf(["Alpha page content", "Beta page content"]),
        filename="report.pdf",
        content_type=PDF_TYPE,
    )

    chunks = (
        db_session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
        .scalars()
        .all()
    )

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert {chunk.page_number for chunk in chunks} == {1, 2}


def test_ingested_chunks_are_embedded(db_session: Session) -> None:
    """Ingestion stores a vector for every chunk.

    Without this an upload reports `indexed` while remaining invisible to
    retrieval, which surfaces to the user as "no relevant results" rather than
    as an error.
    """

    document = ingest_document(
        db_session,
        data=build_text("Some body text."),
        filename="notes.txt",
        content_type="text/plain",
    )

    chunks = (
        db_session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        .scalars()
        .all()
    )

    assert chunks
    assert all(chunk.embedding is not None for chunk in chunks)


def test_every_chunk_is_embedded_exactly_once(
    db_session: Session, fake_embeddings: list[list[str]]
) -> None:
    """Chunk text reaches the provider once, batched, not one call per chunk."""

    document = ingest_document(
        db_session,
        data=build_markdown([("A", "First body."), ("B", "Second body.")]),
        filename="notes.md",
        content_type="text/markdown",
    )

    embedded_texts = [text for batch in fake_embeddings for text in batch]

    assert len(embedded_texts) == document.chunk_count
    assert len(set(embedded_texts)) == len(embedded_texts)


def test_ingested_rows_are_scoped_to_the_owning_user(db_session: Session) -> None:
    """Both the document and its chunks carry the owner id."""

    document = ingest_document(
        db_session,
        data=build_text("Body"),
        filename="notes.txt",
        content_type="text/plain",
        user_id="user-42",
    )

    chunks = (
        db_session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        .scalars()
        .all()
    )

    assert document.user_id == "user-42"
    assert all(chunk.user_id == "user-42" for chunk in chunks)


# --- failure paths -------------------------------------------------------


def test_validation_failure_persists_nothing(db_session: Session) -> None:
    """A rejected upload leaves no document row behind."""

    with pytest.raises(UnsupportedDocumentTypeError):
        ingest_document(
            db_session,
            data=b"body",
            filename="archive.zip",
            content_type="application/zip",
        )

    assert db_session.execute(select(Document)).scalars().all() == []


def test_parse_failure_records_a_failed_document(db_session: Session) -> None:
    """A file that passes validation but cannot be parsed is recorded as
    failed, so the user can see why."""

    with pytest.raises(DocumentParseError):
        ingest_document(
            db_session,
            data=b"this is not a pdf at all",
            filename="broken.pdf",
            content_type=PDF_TYPE,
        )

    document = db_session.execute(select(Document)).scalars().one()
    assert document.status == DocumentStatus.FAILED
    assert document.error_message
    assert document.chunk_count == 0


def test_parse_failure_stores_no_chunks(db_session: Session) -> None:
    """A failed ingest does not leave partial chunks behind."""

    with pytest.raises(DocumentParseError):
        ingest_document(
            db_session,
            data=b"not a pdf",
            filename="broken.pdf",
            content_type=PDF_TYPE,
        )

    assert db_session.execute(select(DocumentChunk)).scalars().all() == []


def test_document_with_no_extractable_text_is_recorded_as_failed(
    db_session: Session,
) -> None:
    """A PDF with no text layer fails visibly rather than indexing empty."""

    with pytest.raises(EmptyDocumentError):
        ingest_document(
            db_session,
            data=build_pdf(["", ""]),
            filename="scanned.pdf",
            content_type=PDF_TYPE,
        )

    document = db_session.execute(select(Document)).scalars().one()
    assert document.status == DocumentStatus.FAILED


def test_embedding_failure_marks_the_document_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document is not `indexed` until it can actually be retrieved."""

    def failing(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_texts", failing)

    with pytest.raises(EmbeddingError):
        ingest_document(
            db_session,
            data=build_text("Some body text."),
            filename="notes.txt",
            content_type="text/plain",
        )

    document = db_session.execute(select(Document)).scalars().one()
    assert document.status == DocumentStatus.FAILED
    assert document.error_message


def test_embedding_failure_keeps_the_parsed_chunks(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike a parse failure, an embedding failure is transient — keeping the
    chunks lets the backfill finish without re-parsing the file."""

    def failing(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_texts", failing)

    with pytest.raises(EmbeddingError):
        ingest_document(
            db_session,
            data=build_text("Some body text."),
            filename="notes.txt",
            content_type="text/plain",
        )

    chunks = db_session.execute(select(DocumentChunk)).scalars().all()
    assert chunks
    assert all(chunk.embedding is None for chunk in chunks)
    assert all(chunk.content for chunk in chunks)


def test_a_failed_embedding_can_be_completed_by_the_backfill(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a failed upload becomes retrievable once the provider
    recovers, with no re-upload."""

    original = embedding_service.embed_texts

    def failing(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_texts", failing)

    with pytest.raises(EmbeddingError):
        ingest_document(
            db_session,
            data=build_text("Some body text."),
            filename="notes.txt",
            content_type="text/plain",
        )

    monkeypatch.setattr(embedding_service, "embed_texts", original)
    result = backfill_missing_embeddings(db_session)

    document = db_session.execute(select(Document)).scalars().one()
    assert result.documents_processed == 1
    assert document.status == DocumentStatus.INDEXED
    assert all(
        chunk.embedding is not None
        for chunk in db_session.execute(select(DocumentChunk)).scalars().all()
    )


def test_deleting_a_document_cascades_to_its_chunks(db_session: Session) -> None:
    """Chunks do not outlive their document."""

    document = ingest_document(
        db_session,
        data=build_text("Body text"),
        filename="notes.txt",
        content_type="text/plain",
    )

    db_session.delete(document)
    db_session.commit()

    assert db_session.execute(select(DocumentChunk)).scalars().all() == []
