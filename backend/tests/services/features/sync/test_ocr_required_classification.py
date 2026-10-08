"""A scan is not a failure, and a corpus report has to say so.

Thirty files in the SunRadia knowledge base are images: the format is
supported, the download succeeded, the parser ran to completion, and there is
no text in the document. Every one of them was counted as an application
failure, which made a healthy run look broken and hid a backlog whose remedy —
optical character recognition — nobody can apply by retrying.

`ocr_required` is therefore its own status, its own ingestion result and its
own sync counter, and this module pins the three claims that make it useful:
the row is persisted so the backlog is queryable, the count stays out of
`failed` so acceptance is not blocked, and the delta token still advances
because these files were genuinely dealt with.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import NO_TEXT_LAYER_TAG
from app.models.document import Document, DocumentStatus, IngestionResult
from app.models.sync import SyncStatus
from app.services.features.documents.ingestion_service import ingest_file
from app.services.features.sync.onedrive_sync_service import SyncResult
from tests.fixtures.factories import build_pdf, build_text
from tests.services.features.sync.test_onedrive_sync_service import (
    SOURCE,
    _client,
    _file,
    _with_content,
    sync_source,
)

#: A PDF with a page and no text on it. What a scan is, structurally.
BLANK_PDF = build_pdf([" "])


def _documents(db: Session) -> list[Document]:
    return list(db.execute(select(Document)).scalars())


class TestIngestionRecordsIt:
    def test_a_scanned_file_is_not_reported_as_a_failure(
        self, db_session: Session
    ) -> None:
        outcome = ingest_file(
            db_session,
            data=BLANK_PDF,
            filename="scan.pdf",
            content_type="application/pdf",
            source_uri="onedrive:d:scan",
            source_version="v1",
        )

        assert outcome.result is IngestionResult.OCR_REQUIRED
        assert outcome.result is not IngestionResult.FAILED

    def test_the_reason_names_the_classification_so_it_can_be_grepped(
        self, db_session: Session
    ) -> None:
        outcome = ingest_file(
            db_session,
            data=BLANK_PDF,
            filename="scan.pdf",
            content_type="application/pdf",
            source_uri="onedrive:d:scan",
            source_version="v1",
        )

        assert NO_TEXT_LAYER_TAG in (outcome.reason or "")

    def test_a_row_is_persisted_so_the_backlog_is_queryable(
        self, db_session: Session
    ) -> None:
        """Counting them was never the problem. Naming them is the point."""

        ingest_file(
            db_session,
            data=BLANK_PDF,
            filename="scan.pdf",
            content_type="application/pdf",
            source_uri="onedrive:d:scan",
            source_version="v1",
        )

        rows = db_session.execute(
            select(Document).where(Document.status == DocumentStatus.OCR_REQUIRED)
        ).scalars()
        names = [row.filename for row in rows]

        assert names == ["scan.pdf"]

    def test_it_is_not_confused_with_an_unsupported_format(
        self, db_session: Session
    ) -> None:
        """A parser for PDF exists. That is the whole difference."""

        outcome = ingest_file(
            db_session,
            data=BLANK_PDF,
            filename="scan.pdf",
            content_type="application/pdf",
            source_uri="onedrive:d:scan",
            source_version="v1",
        )

        assert outcome.status is DocumentStatus.OCR_REQUIRED
        assert outcome.status is not DocumentStatus.UNSUPPORTED

    def test_a_hand_upload_is_classified_the_same_way(
        self, db_session: Session
    ) -> None:
        """The classification is a property of the document, not of the caller.

        A hand upload that will not parse already leaves a row saying why —
        that is what the documents list shows the person — and a scan is no
        different. What it must not be is `failed`, which would put it on a
        list of things to investigate rather than a list of things to OCR.
        """

        outcome = ingest_file(
            db_session,
            data=BLANK_PDF,
            filename="scan.pdf",
            content_type="application/pdf",
        )

        assert outcome.result is IngestionResult.OCR_REQUIRED
        assert [row.status for row in _documents(db_session)] == [
            DocumentStatus.OCR_REQUIRED
        ]

    def test_an_empty_upload_is_not_called_a_scan(self, db_session: Session) -> None:
        """Zero bytes is the caller's mistake, not a property of a document.

        Both raise `EmptyDocumentError`; only the narrower `NoTextLayerError`
        earns an `ocr_required` row, which is why the two are separate types
        rather than separated by reading the message.
        """

        outcome = ingest_file(
            db_session,
            data=b"",
            filename="nothing.pdf",
            content_type="application/pdf",
            source_uri="onedrive:d:nothing",
            source_version="v1",
        )

        assert outcome.result is IngestionResult.FAILED

    def test_a_document_replaced_by_a_scan_leaves_retrieval(
        self, db_session: Session
    ) -> None:
        """Its chunks are kept, and its status is what takes it out of search.

        The same rule a parse failure follows. Retrieval reads only `indexed`
        documents, so nothing stale is reachable either way, and emptying the
        row because today's version is a scan would throw away text the corpus
        still has.
        """

        first = ingest_file(
            db_session,
            data=build_text("Readable prose."),
            filename="doc.txt",
            content_type="text/plain",
            source_uri="onedrive:d:i1",
            source_version="v1",
        )

        assert first.result is IngestionResult.INDEXED

        ingest_file(
            db_session,
            data=build_text("\x00"),
            filename="doc.txt",
            content_type="text/plain",
            source_uri="onedrive:d:i1",
            source_version="v2",
        )

        document = _documents(db_session)[0]

        assert document.status is DocumentStatus.OCR_REQUIRED
        assert document.status is not DocumentStatus.INDEXED


class TestTheSyncCountsItSeparately:
    @pytest.fixture
    def summary(self, db_session: Session):
        client = _client([])
        scan = _file("i1", "scan.pdf", mime_type="application/pdf")
        client.downloads[scan["@microsoft.graph.downloadUrl"]] = BLANK_PDF
        good = _with_content(client, _file("i2", "notes.txt"), "Readable prose.")
        client.delta_pages = [([scan, good], "delta:token-1")]

        return sync_source(db_session, client, SOURCE)

    def test_it_has_a_counter_of_its_own(self, summary) -> None:
        assert summary.ocr_required == 1

    def test_it_is_not_added_to_the_failure_count(self, summary) -> None:
        """The claim that unblocks acceptance."""

        assert summary.failed == 0

    def test_the_run_is_still_a_success(self, summary) -> None:
        assert summary.status is SyncStatus.SUCCEEDED

    def test_the_delta_token_still_advances(self, summary) -> None:
        """These files were dealt with. Withholding the token over them would
        re-walk the whole folder forever to rediscover the same thirty scans."""

        assert summary.delta_advanced is True

    def test_the_readable_file_beside_it_is_indexed(self, summary) -> None:
        assert summary.indexed == 1

    def test_the_outcome_names_the_file(self, summary) -> None:
        scans = [
            item for item in summary.outcomes if item.result is SyncResult.OCR_REQUIRED
        ]

        assert [item.name for item in scans] == ["scan.pdf"]

    def test_a_second_run_does_not_rediscover_it_as_news(
        self, db_session: Session, summary
    ) -> None:
        """The row is what makes the backlog stable rather than re-reported."""

        rows = db_session.execute(
            select(Document).where(Document.status == DocumentStatus.OCR_REQUIRED)
        ).scalars()

        assert [row.filename for row in rows] == ["scan.pdf"]


class TestThePreviouslyFailingShapesNowReachRetrieval:
    """The end of the chain, not the middle of it.

    Each of these is one of the three real files that ended a sync with
    "Ingestion failed unexpectedly (ValueError)". Parsing them is necessary but
    not sufficient — the claim worth making is that they are indexed, hold
    chunks, and come back from a search.
    """

    def test_a_utf16_export_is_indexed_and_searchable(
        self, db_session: Session
    ) -> None:
        from tests.fixtures.factories import build_utf16_html

        outcome = ingest_file(
            db_session,
            data=build_utf16_html(
                [("Functional Processes", "Instantiate a new product record.")]
            ),
            filename="PIM_Functional_Processes_v0.95.html",
            content_type="text/html",
            source_uri="onedrive:d:pim",
            source_version="v1",
        )

        assert outcome.result is IngestionResult.INDEXED

        document = db_session.get(Document, outcome.document_id)

        assert document.chunk_count > 0
        assert "Instantiate a new product record." in document.chunks[0].content

    def test_a_document_carrying_stray_nuls_is_indexed(
        self, db_session: Session
    ) -> None:
        """`Summary View.pdf`: a perfectly good file with thirteen of them."""

        outcome = ingest_file(
            db_session,
            data=build_text("Supplier Data\x00\nPrimary Contact\x00"),
            filename="Summary View.txt",
            content_type="text/plain",
            source_uri="onedrive:d:summary",
            source_version="v1",
        )

        assert outcome.result is IngestionResult.INDEXED

        document = db_session.get(Document, outcome.document_id)
        stored = "".join(chunk.content for chunk in document.chunks)

        assert "\x00" not in stored
        assert "Supplier Data" in stored

    def test_the_sync_reports_them_as_indexed_rather_than_failed(
        self, db_session: Session
    ) -> None:
        from tests.fixtures.factories import build_utf16_html

        client = _client([])
        export = _file("i1", "export.html", mime_type="text/html")
        client.downloads[export["@microsoft.graph.downloadUrl"]] = build_utf16_html(
            [("Roadmap", "Solution delivery, phase one.")]
        )
        client.delta_pages = [([export], "delta:token-1")]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.failed == 0
        assert summary.indexed == 1
        assert summary.status is SyncStatus.SUCCEEDED
