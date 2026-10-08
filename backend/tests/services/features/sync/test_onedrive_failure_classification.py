"""Why a file failed, and what that means for the delta token.

The module that exists because of a real 25-hour run over 847 files that ended
`partial` with 420 failures and a token that had not moved — so the next run
would have done the same thing again, forever.

One bit decides all of it: whether a failure could plausibly succeed next time.
Get it wrong in one direction and a folder is re-walked nightly to collect the
same refusals; get it wrong in the other and a file whose bytes were never
fetched is recorded as dealt with. Every test here pins one side of that line.
"""

from sqlalchemy.orm import Session

from app.models.document import DocumentStatus
from app.models.sync import SyncStatus
from app.services.features.documents.document_service import find_document_by_source
from app.services.features.sync.onedrive_sync_service import (
    SyncResult,
    classify_without_downloading,
    get_state,
    sync_source,
)
from app.services.features.sync.source_config import OneDriveSource
from app.services.graph.drive_service import DriveItem
from tests.fixtures.factories import build_text
from tests.support.graph import DRIVE_ID, FakeGraphClient, drive_item  # noqa: F401

SOURCE = OneDriveSource(
    key="capabilities", label="Capabilities", drive_id=DRIVE_ID, path="Capabilities"
)


def _file(item_id: str, name: str, **kwargs) -> dict:
    return drive_item(item_id, name, **kwargs)


def _url(item: dict) -> str:
    return item["@microsoft.graph.downloadUrl"]


def _readable(client: FakeGraphClient, item: dict, text: str = "Readable.") -> dict:
    client.downloads[_url(item)] = build_text(text)

    return item


def _outcome(summary, name: str):
    return next(item for item in summary.outcomes if item.name == name)


# --- deciding before the network is touched -------------------------------


class TestClassifyingFromMetadata:
    def test_a_legacy_format_is_decided_from_its_name(self) -> None:
        # The whole point: no bytes need to be fetched to know that a `.doc`
        # cannot be indexed. Across the SunRadia folder this is 292 of 847
        # files — a third of the run's downloads, spent to reach a conclusion
        # the filename had already reached.
        item = DriveItem(
            item_id="i1",
            name="Proposal.doc",
            drive_id=DRIVE_ID,
            is_folder=False,
            deleted=False,
            size=4096,
        )

        reason = classify_without_downloading(item)

        assert reason is not None
        assert ".docx" in reason

    def test_a_supported_format_is_left_to_the_download(self) -> None:
        item = DriveItem(
            item_id="i1",
            name="Proposal.docx",
            drive_id=DRIVE_ID,
            is_folder=False,
            deleted=False,
            size=4096,
        )

        assert classify_without_downloading(item) is None

    def test_an_oversized_file_is_decided_from_its_size(self) -> None:
        item = DriveItem(
            item_id="i1",
            name="Huge.pdf",
            drive_id=DRIVE_ID,
            is_folder=False,
            deleted=False,
            size=10**12,
        )

        reason = classify_without_downloading(item)

        assert reason is not None and "was not downloaded" in reason


def test_an_unsupported_file_is_never_downloaded(db_session: Session) -> None:
    """The performance fix, asserted as behaviour rather than as a timing."""

    client = FakeGraphClient()
    legacy = _file("i1", "Old proposal.doc", mime_type="application/msword")
    good = _readable(client, _file("i2", "Current proposal.txt"))
    client.delta_pages = [([legacy, good], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.unsupported == 1
    assert summary.indexed == 1
    # The only URL fetched is the one belonging to the file that can be read.
    assert client.downloaded == [_url(good)]


def test_an_unsupported_file_still_leaves_a_row(db_session: Session) -> None:
    """Without one, every run rediscovers and re-rejects it forever, and
    nothing can show which corpus files the knowledge base is missing."""

    client = FakeGraphClient()
    client.delta_pages = [
        (
            [_file("i1", "Plan.mmap", size=2048, mime_type="application/octet-stream")],
            "delta:token-1",
        )
    ]

    sync_source(db_session, client, SOURCE)

    document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")

    assert document is not None
    assert document.status is DocumentStatus.UNSUPPORTED
    assert "PDF or DOCX" in (document.error_message or "")
    # Its size is known from the listing even though its bytes never were.
    assert document.file_size_bytes == 2048
    assert document.content_hash is None


def test_an_unsupported_file_does_not_hold_the_token_back(db_session: Session) -> None:
    client = FakeGraphClient()
    client.delta_pages = [
        ([_file("i1", "Old.doc", mime_type="application/msword")], "delta:token-1")
    ]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.status is SyncStatus.SUCCEEDED
    assert summary.delta_advanced is True
    assert get_state(db_session, "capabilities").delta_link == "delta:token-1"


# --- the expired download URL ---------------------------------------------


class TestExpiredDownloadUrl:
    def test_an_expired_url_is_re_minted_rather_than_failed(
        self, db_session: Session
    ) -> None:
        """The defect that cost the 25-hour run, in one test.

        Graph's pre-authorised URLs live about an hour. A run that enumerates
        847 files and then downloads them one at a time is holding URLs minted
        before it started, and every file it reaches after the first hour was
        being recorded as a failure — for a file whose bytes are perfectly
        readable.
        """

        client = FakeGraphClient()
        item = _file("i1", "Proposal.txt")
        stale = _url(item)

        fresh = "https://files.example/i1?minted=now"

        client.expired_urls.add(stale)
        client.fresh_urls["i1"] = fresh
        client.downloads[fresh] = build_text("Readable.")
        client.delta_pages = [([item], "delta:token-1")]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.indexed == 1
        assert client.refreshed == ["i1"]
        assert summary.delta_advanced is True

    def test_the_url_is_re_minted_once_and_not_in_a_loop(
        self, db_session: Session
    ) -> None:
        # A refreshed URL that is also refused is telling us something real.
        # Asking again would be an unbounded retry against the one status that
        # will not change.
        client = FakeGraphClient()
        item = _file("i1", "Secret.txt")

        client.expired_urls.add(_url(item))
        client.fresh_urls["i1"] = "https://files.example/i1?minted=now"
        client.forbidden_urls.add("https://files.example/i1?minted=now")
        client.delta_pages = [([item], "delta:token-1")]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.failed == 1
        assert client.refreshed == ["i1"]

    def test_a_file_the_application_cannot_read_does_not_stall_the_folder(
        self, db_session: Session
    ) -> None:
        """A permanent refusal must not hold the token back.

        This is the inverse of the rule and the half that was missing. A file
        nothing is allowed to open will be refused identically tomorrow, so a
        token withheld for it is withheld forever — and the other 846 files
        are re-walked every run to reach the same answer.
        """

        client = FakeGraphClient()
        refused = _file("i1", "Secret.txt")
        good = _readable(client, _file("i2", "Public.txt"))

        client.forbidden_urls.add(_url(refused))
        client.delta_pages = [([refused, good], "delta:token-1")]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.failed == 1
        assert summary.indexed == 1
        # `partial`, not `succeeded`: a run that failed a file has not dealt
        # with every file, and saying otherwise is how 487 unread files came to
        # be reported as a success. The token still advances — the refusal is
        # permanent and one file *was* fetched.
        assert summary.status is SyncStatus.PARTIAL
        assert summary.delta_advanced is True
        assert _outcome(summary, "Secret.txt").transient is False


# --- transient failures still hold the token ------------------------------


class TestTransientFailures:
    def test_a_throttled_download_holds_the_token_back(
        self, db_session: Session
    ) -> None:
        """The rule the module was built around, and it still holds.

        A 5xx or an exhausted 429 means the content was never seen. Moving the
        token past it would bury the file until it changed of its own accord.
        """

        client = FakeGraphClient()
        throttled = _file("i1", "Busy.txt")
        good = _readable(client, _file("i2", "Fine.txt"))

        client.fail_downloads.add(_url(throttled))
        client.delta_pages = [([throttled, good], "delta:token-1")]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.status is SyncStatus.PARTIAL
        assert summary.delta_advanced is False
        assert get_state(db_session, "capabilities").delta_link is None
        assert _outcome(summary, "Busy.txt").transient is True

    def test_the_files_that_did_land_are_still_indexed(
        self, db_session: Session
    ) -> None:
        client = FakeGraphClient()
        throttled = _file("i1", "Busy.txt")
        good = _readable(client, _file("i2", "Fine.txt"), "Kept.")

        client.fail_downloads.add(_url(throttled))
        client.delta_pages = [([throttled, good], "delta:token-1")]

        sync_source(db_session, client, SOURCE)

        document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i2")

        assert document is not None and document.status is DocumentStatus.INDEXED

    def test_the_recorded_reason_names_the_causes(self, db_session: Session) -> None:
        """ "405 file(s) failed transiently" was all a failed run left behind.

        It cannot distinguish an expired URL from a throttled one from a folder
        nobody consented to, and those have three different remedies — so a run
        nobody watched left no way to find out what had happened.
        """

        client = FakeGraphClient()
        first = _file("i1", "One.txt")
        second = _file("i2", "Two.txt")

        client.fail_downloads.update({_url(first), _url(second)})
        client.delta_pages = [([first, second], "delta:token-1")]

        sync_source(db_session, client, SOURCE)
        message = get_state(db_session, "capabilities").error_message or ""

        assert "2 transient, 0 permanent" in message
        assert "HTTP 500" in message
        assert "(2)" in message


# --- ingestion failures are permanent -------------------------------------


class TestIngestionFailures:
    def test_a_corrupt_file_does_not_hold_the_token_back(
        self, db_session: Session
    ) -> None:
        # A PDF that will not parse parses no better tomorrow. It is recorded
        # against the document and the folder moves on.
        client = FakeGraphClient()
        broken = _file("i1", "Broken.pdf", mime_type="application/pdf")
        client.downloads[_url(broken)] = b"not a pdf at all"
        client.delta_pages = [([broken], "delta:token-1")]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.failed == 1
        assert summary.status is SyncStatus.PARTIAL
        # The bytes arrived perfectly well and simply will not parse, so this
        # is not evidence of a broken downloader and the token moves on.
        assert _outcome(summary, "Broken.pdf").downloaded is True
        assert summary.delta_advanced is True
        assert _outcome(summary, "Broken.pdf").transient is False

    def test_the_failure_is_visible_on_the_document(self, db_session: Session) -> None:
        client = FakeGraphClient()
        broken = _file("i1", "Broken.pdf", mime_type="application/pdf")
        client.downloads[_url(broken)] = b"not a pdf at all"
        client.delta_pages = [([broken], "delta:token-1")]

        sync_source(db_session, client, SOURCE)
        document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")

        assert document is not None
        assert document.status is DocumentStatus.FAILED
        assert document.error_message


# --- batching -------------------------------------------------------------


def test_every_file_is_processed_across_batch_boundaries(
    db_session: Session, monkeypatch
) -> None:
    """Batching is an implementation detail and must stay one.

    With a batch smaller than the folder, the same files arrive, in the same
    order, with the same outcomes.
    """

    from app.config.settings import settings

    monkeypatch.setattr(settings, "ONEDRIVE_DOWNLOAD_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "ONEDRIVE_DOWNLOAD_CONCURRENCY", 2)

    client = FakeGraphClient()
    items = [
        _readable(client, _file(f"i{n}", f"file-{n}.txt"), f"Document {n}.")
        for n in range(7)
    ]
    client.delta_pages = [(items, "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.discovered == 7
    assert summary.indexed == 7
    assert [outcome.name for outcome in summary.outcomes] == [
        f"file-{n}.txt" for n in range(7)
    ]
    assert summary.delta_advanced is True


def test_a_deletion_inside_a_batch_is_still_a_deletion(db_session: Session) -> None:
    client = FakeGraphClient()
    good = _readable(client, _file("i1", "Kept.txt"))
    client.delta_pages = [
        ([good], "delta:token-1"),
        ([_file("i1", "Kept.txt", deleted=True)], "delta:token-2"),
    ]

    sync_source(db_session, client, SOURCE)
    summary = sync_source(db_session, client, SOURCE)

    assert summary.deleted == 1
    assert find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1") is None
    assert summary.outcomes[0].result is SyncResult.DELETED


# --- concurrency ----------------------------------------------------------


def test_downloads_in_one_batch_actually_overlap(
    db_session: Session, monkeypatch
) -> None:
    """Proved with a barrier rather than with a stopwatch.

    A timing assertion is a flaky test. A barrier is not: if the downloads were
    serial, the first thread would wait for a partner that cannot arrive until
    it returns, and the barrier would time out.
    """

    import threading

    from app.config.settings import settings

    monkeypatch.setattr(settings, "ONEDRIVE_DOWNLOAD_CONCURRENCY", 3)
    monkeypatch.setattr(settings, "ONEDRIVE_DOWNLOAD_BATCH_SIZE", 3)

    barrier = threading.Barrier(3, timeout=5)
    client = FakeGraphClient()
    real_download = client.download

    def gated(url, **kwargs):
        barrier.wait()
        return real_download(url, **kwargs)

    monkeypatch.setattr(client, "download", gated)

    items = [
        _readable(client, _file(f"i{n}", f"file-{n}.txt"), f"Document {n}.")
        for n in range(3)
    ]
    client.delta_pages = [(items, "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.indexed == 3


def test_ingestion_stays_on_the_calling_thread(
    db_session: Session, monkeypatch
) -> None:
    """`Session` is not thread-safe, and handing one to a pool is a corruption
    bug that only appears under load — exactly the load 847 files produce."""

    import threading

    from app.config.settings import settings
    from app.services.features.sync import onedrive_sync_service

    monkeypatch.setattr(settings, "ONEDRIVE_DOWNLOAD_CONCURRENCY", 4)
    monkeypatch.setattr(settings, "ONEDRIVE_DOWNLOAD_BATCH_SIZE", 4)

    caller = threading.get_ident()
    threads: list[int] = []
    real_ingest = onedrive_sync_service.ingest_staged_file

    def watched(*args, **kwargs):
        threads.append(threading.get_ident())
        return real_ingest(*args, **kwargs)

    monkeypatch.setattr(onedrive_sync_service, "ingest_staged_file", watched)

    client = FakeGraphClient()
    items = [
        _readable(client, _file(f"i{n}", f"file-{n}.txt"), f"Document {n}.")
        for n in range(4)
    ]
    client.delta_pages = [(items, "delta:token-1")]

    sync_source(db_session, client, SOURCE)

    assert threads == [caller] * 4


# --- very large files -----------------------------------------------------


class TestFilesLargerThanTheCeiling:
    """The file that ended the first real sync of SunRadia's Knowledge Base.

    `CFTC Debrief _ Microsoft Teams 2024-09-05 08-58-41.mp4`, 6,788,146,857
    bytes. Rejected for its size, which was always the intent — and then
    recorded, which is where it overflowed `documents.file_size_bytes`.

    These run on SQLite, whose INTEGER is 64-bit, so they cannot see the column
    type; `tests/database/test_migration_fidelity.py` asserts that against real
    PostgreSQL. What they pin is the call path: the size is carried through
    whole, never clamped to fit.
    """

    OVERSIZED = 6_788_146_857

    def test_a_multi_gigabyte_file_is_rejected_without_being_downloaded(
        self, db_session: Session
    ) -> None:
        client = FakeGraphClient()
        client.delta_pages = [
            (
                [
                    _file(
                        "i1",
                        "CFTC Debrief _ Microsoft Teams 2024-09-05 08-58-41.mp4",
                        size=self.OVERSIZED,
                        mime_type="video/mp4",
                    )
                ],
                "delta:token-1",
            )
        ]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.unsupported == 1
        assert summary.failed == 0
        # Not a byte of a 6.8 GB file was fetched to reach that conclusion.
        assert client.downloaded == []

    def test_the_reported_size_is_recorded_whole_not_clamped(
        self, db_session: Session
    ) -> None:
        """Recording 2,147,483,647 for a 6.8 GB file would be a lie in the one
        column whose purpose is to say how big the thing nobody could ingest is."""

        client = FakeGraphClient()
        client.delta_pages = [
            (
                [_file("i1", "huge.mp4", size=self.OVERSIZED, mime_type="video/mp4")],
                "delta:token-1",
            )
        ]

        sync_source(db_session, client, SOURCE)

        document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")

        assert document is not None
        assert document.file_size_bytes == self.OVERSIZED
        assert document.status is DocumentStatus.UNSUPPORTED

    def test_the_oversized_file_does_not_stall_the_folder(
        self, db_session: Session
    ) -> None:
        """The real cost of the overflow was not one lost file.

        The database error aborted the surrounding transaction, so the whole
        run ended. With the size recordable, the file is one `unsupported` row
        and the rest of the folder carries on.
        """

        client = FakeGraphClient()
        good = _readable(client, _file("i2", "Readable.txt"))
        client.delta_pages = [
            (
                [
                    _file("i1", "huge.mp4", size=self.OVERSIZED, mime_type="video/mp4"),
                    good,
                ],
                "delta:token-1",
            )
        ]

        summary = sync_source(db_session, client, SOURCE)

        assert (summary.unsupported, summary.indexed) == (1, 1)
        assert summary.status is SyncStatus.SUCCEEDED
        assert summary.delta_advanced is True


# --- a run that dies mid-loop ---------------------------------------------


class TestAbandonedRuns:
    """What a database error does to a source, as opposed to a file error.

    The per-file guards cover downloads and ingestion. They do not cover the
    database refusing a write, which poisons the transaction rather than one
    item — and that is what a 6.8 GB file did through `NumericValueOutOfRange`
    before `documents.file_size_bytes` became `BIGINT`.
    """

    @staticmethod
    def _exploding(monkeypatch, after: int) -> None:
        """Make ingestion raise once `after` files have been handled."""

        from app.services.features.sync import onedrive_sync_service

        real = onedrive_sync_service.ingest_staged_file
        seen: list[int] = []

        def guarded(*args, **kwargs):
            seen.append(1)

            if len(seen) > after:
                raise RuntimeError("integer out of range")

            return real(*args, **kwargs)

        monkeypatch.setattr(onedrive_sync_service, "ingest_staged_file", guarded)

    def _folder(self, client: FakeGraphClient, count: int) -> None:
        client.delta_pages = [
            (
                [
                    _readable(client, _file(f"i{n}", f"file-{n}.txt"), f"Document {n}.")
                    for n in range(count)
                ],
                "delta:token-1",
            )
        ]

    def test_the_source_is_released_rather_than_left_running(
        self, db_session: Session, monkeypatch
    ) -> None:
        """The defect this class exists for.

        `running` is committed before the work starts, so an exception escaping
        `sync_source` leaves the row saying `running` — and the rollback in
        `sync_all` cannot undo a committed write. The source then refuses every
        sync until `_is_stale` forgives it six hours later.
        """

        client = FakeGraphClient()
        self._folder(client, 4)
        self._exploding(monkeypatch, after=2)

        summary = sync_source(db_session, client, SOURCE)

        state = get_state(db_session, "capabilities")

        assert state.status is SyncStatus.FAILED
        assert state.status is not SyncStatus.RUNNING
        assert summary.status is SyncStatus.FAILED

    def test_a_later_run_is_not_locked_out(
        self, db_session: Session, monkeypatch
    ) -> None:
        """The consequence that is actually felt: pressing sync again works."""

        client = FakeGraphClient()
        self._folder(client, 3)
        self._exploding(monkeypatch, after=1)
        sync_source(db_session, client, SOURCE)

        monkeypatch.undo()

        second = FakeGraphClient()
        self._folder(second, 3)

        # Would raise SyncAlreadyRunningError if the flag had been left behind.
        # The point is that the run *happened*; what it made of the files is a
        # separate question, and in this environment embedding is unavailable.
        recovered = sync_source(db_session, second, SOURCE)

        assert recovered.status is not SyncStatus.RUNNING
        assert recovered.discovered == 3

    def test_the_delta_token_does_not_advance(
        self, db_session: Session, monkeypatch
    ) -> None:
        """The run did not finish, so the promise the token makes is false."""

        client = FakeGraphClient()
        self._folder(client, 4)
        self._exploding(monkeypatch, after=2)

        summary = sync_source(db_session, client, SOURCE)

        assert summary.delta_advanced is False
        assert get_state(db_session, "capabilities").delta_link is None

    def test_the_work_already_done_is_still_reported(
        self, db_session: Session, monkeypatch
    ) -> None:
        """Files ingested before the failure were committed one at a time, so
        they really are in the corpus. Reporting zero would misdescribe it."""

        client = FakeGraphClient()
        self._folder(client, 4)
        self._exploding(monkeypatch, after=2)

        summary = sync_source(db_session, client, SOURCE)

        assert summary.discovered == 4
        assert summary.indexed == 2
        first = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i0")
        assert first is not None

    def test_the_reason_says_how_far_it_got(
        self, db_session: Session, monkeypatch
    ) -> None:
        client = FakeGraphClient()
        self._folder(client, 4)
        self._exploding(monkeypatch, after=2)

        sync_source(db_session, client, SOURCE)
        message = get_state(db_session, "capabilities").error_message or ""

        assert "2 of 4" in message
        assert "RuntimeError" in message
        # No stack trace, no query, no connection string.
        assert "integer out of range" not in message


# --- the run that indexed nothing -----------------------------------------


class TestABlindRun:
    """A live run over 859 files that ended `succeeded`, 0 indexed, 487 failed,
    and advanced its delta token past every file it had failed to read.

    Two defects, and the second is the dangerous one. The downloader was broken
    — so every download failed. And because each failure was classified
    *permanent*, the run called itself a success and moved the token, which in a
    delta protocol means those 487 files are never offered again. One bug lost
    the corpus; the other hid it and made the loss irreversible.
    """

    def _all_failing(self, count: int = 3) -> FakeGraphClient:
        client = FakeGraphClient()
        items = [_file(f"i{n}", f"file-{n}.txt") for n in range(count)]

        for item in items:
            # Refused outright, freshly minted or not: permanent, every one.
            client.forbidden_urls.add(_url(item))
            client.fresh_urls[f"i{items.index(item)}"] = _url(item)

        client.delta_pages = [(items, "delta:token-1")]

        return client

    def test_a_run_that_read_nothing_does_not_advance_the_token(
        self, db_session: Session
    ) -> None:
        """The rule that would have saved the corpus.

        Every download failing is far better explained by a broken download
        path than by that many files each being independently unreadable, so
        the classification is not trusted and the token stays put.
        """

        summary = sync_source(db_session, self._all_failing(), SOURCE)

        assert summary.indexed == 0
        assert summary.failed == 3
        assert summary.delta_advanced is False
        assert get_state(db_session, "capabilities").delta_link is None

    def test_a_run_that_read_nothing_is_not_called_a_success(
        self, db_session: Session
    ) -> None:
        summary = sync_source(db_session, self._all_failing(), SOURCE)

        assert summary.status is SyncStatus.PARTIAL

    def test_the_reason_says_the_downloader_is_suspect(
        self, db_session: Session
    ) -> None:
        sync_source(db_session, self._all_failing(), SOURCE)
        message = get_state(db_session, "capabilities").error_message or ""

        assert "0 transient, 3 permanent" in message
        assert "broken download path" in message

    def test_one_success_is_enough_to_trust_the_classification(
        self, db_session: Session
    ) -> None:
        """The guard must not fire whenever anything fails.

        A folder where most files are refused but one reads fine is a folder
        with permissions problems, not a broken downloader — and holding the
        token back there would re-walk it forever, which is the defect this
        project already fixed once.
        """

        client = self._all_failing()
        good = _readable(client, _file("i9", "Readable.txt"))
        items, link = client.delta_pages[0]
        client.delta_pages = [(items + [good], link)]

        summary = sync_source(db_session, client, SOURCE)

        assert summary.indexed == 1
        assert summary.failed == 3
        assert summary.delta_advanced is True

    def test_every_failure_leaves_a_queryable_row(self, db_session: Session) -> None:
        """What makes "counts by extension, MIME type and reason" answerable.

        Before this, a download failure recorded a number and nothing else.
        """

        sync_source(db_session, self._all_failing(), SOURCE)

        rows = [
            find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i{n}")
            for n in range(3)
        ]

        assert all(row is not None for row in rows)
        assert all(row.status is DocumentStatus.FAILED for row in rows)
        assert all("403" in (row.error_message or "") for row in rows)
        # The metadata a breakdown needs, from the listing rather than the bytes.
        assert all(row.filename.endswith(".txt") for row in rows)
        assert all(row.content_type == "text/plain" for row in rows)

    def test_a_failed_file_keeps_the_chunks_it_had(self, db_session: Session) -> None:
        """One bad night must not empty the corpus.

        A document that indexed last week and whose download failed today should
        keep answering queries until it is successfully replaced.
        """

        client = FakeGraphClient()
        item = _readable(client, _file("i1", "Proposal.txt"), "Solar EPC capability.")
        client.delta_pages = [([item], "delta:token-1")]
        sync_source(db_session, client, SOURCE)

        indexed = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")
        chunks_before = indexed.chunk_count
        assert chunks_before > 0

        broken = FakeGraphClient()
        moved = _file("i1", "Proposal.txt", version="v2")
        broken.forbidden_urls.add(_url(moved))
        broken.fresh_urls["i1"] = _url(moved)
        broken.delta_pages = [([moved], "delta:token-2")]
        sync_source(db_session, broken, SOURCE)

        after = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")

        assert after.status is DocumentStatus.FAILED
        assert after.chunk_count == chunks_before
