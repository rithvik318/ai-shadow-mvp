"""What makes two uploads the same document, and what happens when one changes.

These are the assertions the later OneDrive synchronisation rests on. A sync
that cannot tell "already have this" from "this is new" either duplicates the
corpus on every run or misses every edit, and both failures are silent — the
knowledge base still answers, just from the wrong number of copies.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import DocumentParseError, UnsupportedDocumentTypeError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.documents.ingestion_service import (
    IngestionResult,
    content_digest,
    ingest_document,
    ingest_file,
)
from tests.fixtures.factories import build_markdown, build_text

PDF_TYPE = "application/pdf"
TEXT_TYPE = "text/plain"


def _chunks_of(db: Session, document: Document) -> list[DocumentChunk]:
    return list(
        db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
        .scalars()
        .all()
    )


def _documents(db: Session) -> list[Document]:
    return list(db.execute(select(Document)).scalars().all())


# --- content identity ----------------------------------------------------


def test_ingestion_records_the_content_hash(db_session: Session) -> None:
    data = build_text("SunRadia delivered MDM for a large bank.")

    document = ingest_document(
        db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE
    )

    assert document.content_hash == content_digest(data)


def test_the_same_bytes_do_not_create_a_second_document(db_session: Session) -> None:
    """The property a sync run depends on: re-offering the corpus is free."""

    data = build_text("SunRadia delivered MDM for a large bank.")

    first = ingest_document(
        db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE
    )
    second = ingest_document(
        db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE
    )

    assert second.id == first.id
    assert len(_documents(db_session)) == 1


def test_a_repeat_upload_is_reported_as_unchanged(db_session: Session) -> None:
    data = build_text("Body")

    ingest_file(db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE)
    outcome = ingest_file(
        db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE
    )

    assert outcome.result is IngestionResult.UNCHANGED
    assert outcome.succeeded


def test_a_repeat_upload_does_not_re_embed(
    db_session: Session, fake_embeddings: list[list[str]]
) -> None:
    """Skipping is not just about row count. Re-embedding an unchanged corpus
    is the cost that makes a nightly sync unaffordable."""

    data = build_text("Body")

    ingest_document(db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE)
    batches_after_first = len(fake_embeddings)

    ingest_document(db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE)

    assert len(fake_embeddings) == batches_after_first


def test_the_same_bytes_under_a_different_name_are_still_one_document(
    db_session: Session,
) -> None:
    """Identity is the content, not the name it arrived under."""

    data = build_text("Body")

    first = ingest_document(
        db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE
    )
    second = ingest_document(
        db_session, data=data, filename="renamed.txt", content_type=TEXT_TYPE
    )

    assert second.id == first.id


def test_different_documents_sharing_a_filename_stay_separate(
    db_session: Session,
) -> None:
    """Two clients each send `proposal.txt`. They are not the same proposal."""

    first = ingest_document(
        db_session,
        data=build_text("Proposal for client A."),
        filename="proposal.txt",
        content_type=TEXT_TYPE,
    )
    second = ingest_document(
        db_session,
        data=build_text("Proposal for client B."),
        filename="proposal.txt",
        content_type=TEXT_TYPE,
    )

    assert first.id != second.id
    assert len(_documents(db_session)) == 2


def test_identity_is_scoped_to_the_owner(db_session: Session) -> None:
    """The knowledge base is shared, but the column is still per-owner, and a
    second owner's identical file must not resolve to the first owner's row."""

    data = build_text("Body")

    first = ingest_document(
        db_session,
        data=data,
        filename="notes.txt",
        content_type=TEXT_TYPE,
        user_id="owner-a",
    )
    second = ingest_document(
        db_session,
        data=data,
        filename="notes.txt",
        content_type=TEXT_TYPE,
        user_id="owner-b",
    )

    assert first.id != second.id


def test_a_document_with_no_hash_is_never_treated_as_a_duplicate(
    db_session: Session,
) -> None:
    """Rows predating the content_hash column carry NULL. NULL is not an
    identity, and must not silently match the next thing uploaded."""

    legacy = Document(
        user_id="mvp-user",
        filename="legacy.txt",
        content_type=TEXT_TYPE,
        file_size_bytes=4,
        content_hash=None,
        status=DocumentStatus.INDEXED,
        chunk_count=0,
    )
    db_session.add(legacy)
    db_session.commit()

    fresh = ingest_document(
        db_session,
        data=build_text("Body"),
        filename="legacy.txt",
        content_type=TEXT_TYPE,
    )

    assert fresh.id != legacy.id


# --- source identity and re-indexing -------------------------------------


def test_a_changed_file_from_the_same_source_is_re_indexed_in_place(
    db_session: Session,
) -> None:
    """The whole point of source_uri: an edit is a new version of a document,
    not a new document."""

    first = ingest_document(
        db_session,
        data=build_text("The original body."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
        source_version="v1",
    )
    second = ingest_document(
        db_session,
        data=build_text("The revised body, which says something else."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
        source_version="v2",
    )

    assert second.id == first.id
    assert second.source_version == "v2"
    assert len(_documents(db_session)) == 1


def test_re_indexing_is_reported_as_replaced(db_session: Session) -> None:
    ingest_file(
        db_session,
        data=build_text("Original."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    outcome = ingest_file(
        db_session,
        data=build_text("Revised."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    assert outcome.result is IngestionResult.REPLACED
    assert outcome.succeeded


def test_replacement_removes_the_old_chunks(db_session: Session) -> None:
    """Stale chunks are the failure that does not announce itself: the document
    looks right, and retrieval quietly answers from text that was deleted."""

    document = ingest_document(
        db_session,
        data=build_markdown([("Heading", "The original paragraph of text.")]),
        filename="notes.md",
        content_type="text/markdown",
        source_uri="onedrive:item-1",
    )
    original_chunk_ids = {chunk.id for chunk in _chunks_of(db_session, document)}

    ingest_document(
        db_session,
        data=build_markdown([("Heading", "A completely different paragraph.")]),
        filename="notes.md",
        content_type="text/markdown",
        source_uri="onedrive:item-1",
    )

    surviving = _chunks_of(db_session, document)

    assert original_chunk_ids.isdisjoint({chunk.id for chunk in surviving})
    assert not any("original paragraph" in chunk.content for chunk in surviving)
    assert any("completely different" in chunk.content for chunk in surviving)


def test_replacement_leaves_no_orphaned_chunks(db_session: Session) -> None:
    """The count on the document has to match what is actually stored, or the
    chunk_count reported by the API is fiction."""

    document = ingest_document(
        db_session,
        data=build_markdown([("A", "x" * 4000)]),
        filename="notes.md",
        content_type="text/markdown",
        source_uri="onedrive:item-1",
    )

    ingest_document(
        db_session,
        data=build_markdown([("A", "short")]),
        filename="notes.md",
        content_type="text/markdown",
        source_uri="onedrive:item-1",
    )

    stored = _chunks_of(db_session, document)

    assert len(stored) == document.chunk_count
    assert [chunk.chunk_index for chunk in stored] == list(range(len(stored)))


def test_re_indexed_chunks_are_embedded(db_session: Session) -> None:
    """A replaced document that kept its status but lost its vectors would be
    indexed and unfindable at the same time."""

    document = ingest_document(
        db_session,
        data=build_text("Original."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    ingest_document(
        db_session,
        data=build_text("Revised, and quite different."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    chunks = _chunks_of(db_session, document)

    assert chunks
    assert all(chunk.embedding is not None for chunk in chunks)
    assert document.status is DocumentStatus.INDEXED


def test_the_same_source_offering_the_same_bytes_is_unchanged(
    db_session: Session,
) -> None:
    data = build_text("Body")

    first = ingest_document(
        db_session,
        data=data,
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    chunk_ids = {chunk.id for chunk in _chunks_of(db_session, first)}

    ingest_document(
        db_session,
        data=data,
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    assert {chunk.id for chunk in _chunks_of(db_session, first)} == chunk_ids


def test_two_sources_holding_identical_bytes_remain_separate(
    db_session: Session,
) -> None:
    """The same file in two OneDrive folders is two things to keep in sync,
    even though its contents are one thing."""

    data = build_text("A shared boilerplate page.")

    first = ingest_document(
        db_session,
        data=data,
        filename="boilerplate.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    second = ingest_document(
        db_session,
        data=data,
        filename="boilerplate.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-2",
    )

    assert first.id != second.id


def test_a_hand_upload_does_not_adopt_a_synced_document(db_session: Session) -> None:
    """Matching on content alone would re-point a synced document at an upload
    it did not come from, and the next sync would then fight over it."""

    data = build_text("Body")

    synced = ingest_document(
        db_session,
        data=data,
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    manual = ingest_document(
        db_session, data=data, filename="notes.txt", content_type=TEXT_TYPE
    )

    assert manual.id != synced.id
    assert synced.source_uri == "onedrive:item-1"
    assert manual.source_uri is None


# --- retrying what failed ------------------------------------------------


def test_re_uploading_after_a_failure_retries_rather_than_skipping(
    db_session: Session,
) -> None:
    """A failed document is not a record of success to be deduplicated
    against. The same bytes offered again must be attempted again."""

    with pytest.raises(DocumentParseError):
        ingest_document(
            db_session,
            data=b"this is not a pdf at all",
            filename="broken.pdf",
            content_type=PDF_TYPE,
        )

    failed = db_session.execute(select(Document)).scalars().one()
    assert failed.status is DocumentStatus.FAILED

    with pytest.raises(DocumentParseError):
        ingest_document(
            db_session,
            data=b"this is not a pdf at all",
            filename="broken.pdf",
            content_type=PDF_TYPE,
        )

    assert len(_documents(db_session)) == 1


def test_a_parse_failure_on_re_index_keeps_the_document_out_of_retrieval(
    db_session: Session,
) -> None:
    """The old chunks survive an unreadable replacement — but the status is
    what governs retrieval, and it says failed, so nothing stale is served."""

    document = ingest_document(
        db_session,
        data=build_text("The original body."),
        filename="notes.pdf",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    with pytest.raises(DocumentParseError):
        ingest_document(
            db_session,
            data=b"not a pdf",
            filename="notes.pdf",
            content_type=PDF_TYPE,
            source_uri="onedrive:item-1",
        )

    db_session.refresh(document)

    assert document.status is DocumentStatus.FAILED
    assert document.error_message


# --- unsupported formats -------------------------------------------------


def test_a_hand_uploaded_unsupported_file_still_raises(db_session: Session) -> None:
    """The single-file contract is unchanged: 415, and no row."""

    with pytest.raises(UnsupportedDocumentTypeError):
        ingest_document(
            db_session,
            data=b"legacy binary",
            filename="proposal.doc",
            content_type="application/msword",
        )

    assert _documents(db_session) == []


def test_an_unsupported_file_is_reported_not_dropped(db_session: Session) -> None:
    outcome = ingest_file(
        db_session,
        data=b"legacy binary",
        filename="proposal.doc",
        content_type="application/msword",
    )

    assert outcome.result is IngestionResult.UNSUPPORTED
    assert not outcome.succeeded
    assert "proposal.doc" == outcome.filename
    assert outcome.reason
    assert _documents(db_session) == []


def test_an_unsupported_file_from_a_source_is_recorded(db_session: Session) -> None:
    """A sync has to remember what it cannot handle. Without a row it would
    rediscover and re-reject the same file on every run, and nothing could
    report which corpus files the knowledge base is missing."""

    outcome = ingest_file(
        db_session,
        data=b"legacy binary",
        filename="proposal.doc",
        content_type="application/msword",
        source_uri="onedrive:item-9",
    )

    document = db_session.execute(select(Document)).scalars().one()

    assert outcome.document_id == document.id
    assert document.status is DocumentStatus.UNSUPPORTED
    assert document.error_message
    assert document.chunk_count == 0


def test_a_document_that_becomes_unsupported_loses_its_chunks(
    db_session: Session,
) -> None:
    """Otherwise the row says it holds nothing while its old text is still
    sitting in document_chunks."""

    document = ingest_document(
        db_session,
        data=build_text("The original body."),
        filename="notes.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    assert _chunks_of(db_session, document)

    ingest_file(
        db_session,
        data=b"legacy binary",
        filename="notes.doc",
        content_type="application/msword",
        source_uri="onedrive:item-1",
    )

    db_session.refresh(document)

    assert document.status is DocumentStatus.UNSUPPORTED
    assert _chunks_of(db_session, document) == []
    assert document.chunk_count == 0


# --- outcomes ------------------------------------------------------------


def test_a_successful_outcome_carries_the_document(db_session: Session) -> None:
    outcome = ingest_file(
        db_session,
        data=build_text("Body"),
        filename="notes.txt",
        content_type=TEXT_TYPE,
    )

    assert outcome.result is IngestionResult.INDEXED
    assert outcome.succeeded
    assert outcome.document_id is not None
    assert outcome.status is DocumentStatus.INDEXED
    assert outcome.reason is None


def test_a_parse_failure_outcome_points_at_the_failed_document(
    db_session: Session,
) -> None:
    """`ingest_file` reports rather than raises, but it must still leave the
    caller able to find the row and show the user why."""

    outcome = ingest_file(
        db_session,
        data=b"this is not a pdf at all",
        filename="broken.pdf",
        content_type=PDF_TYPE,
    )

    assert outcome.result is IngestionResult.FAILED
    assert not outcome.succeeded
    assert outcome.document_id is not None
    assert outcome.status is DocumentStatus.FAILED
    assert outcome.reason


def test_an_empty_file_outcome_has_no_document(db_session: Session) -> None:
    """Rejected before a row existed, so there is nothing to point at."""

    outcome = ingest_file(
        db_session, data=b"", filename="notes.txt", content_type=TEXT_TYPE
    )

    assert outcome.result is IngestionResult.FAILED
    assert outcome.document_id is None
    assert outcome.reason
    assert _documents(db_session) == []
