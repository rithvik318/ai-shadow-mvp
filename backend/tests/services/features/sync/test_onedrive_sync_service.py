"""Synchronisation, with Graph replaced and the knowledge base real.

The database, ingestion, chunking and embedding are all the production ones —
only Graph is a double. That is deliberate: the questions worth asking here
are "did the right document end up in the corpus" and "is it safe to resume",
and neither can be answered by asserting which methods were called.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    SyncAlreadyRunningError,
    SyncNotConfiguredError,
    SyncSourceNotFoundError,
)
from app.models.document import Document, DocumentStatus
from app.models.sync import SyncStatus
from app.services.features.documents.document_service import find_document_by_source
from app.services.features.sync.onedrive_sync_service import (
    SyncResult,
    get_state,
    sync_all,
    sync_source,
)
from app.services.features.sync.source_config import OneDriveSource
from tests.fixtures.factories import build_text
from tests.support.graph import DRIVE_ID, FakeGraphClient, drive_item, sources_json

SOURCE = OneDriveSource(
    key="capabilities", label="Capabilities", drive_id=DRIVE_ID, path="Capabilities"
)


def _client(pages, downloads=None, **kwargs) -> FakeGraphClient:
    client = FakeGraphClient(delta_pages=pages, **kwargs)

    for item, content in (downloads or {}).items():
        client.downloads[item] = content

    return client


def _file(item_id: str, name: str, *, version: str = "v1", **kwargs) -> dict:
    return drive_item(item_id, name, version=version, **kwargs)


def _with_content(client: FakeGraphClient, item: dict, text: str) -> dict:
    client.downloads[item["@microsoft.graph.downloadUrl"]] = build_text(text)

    return item


def _documents(db: Session) -> list[Document]:
    from sqlalchemy import select

    return list(db.execute(select(Document)).scalars().all())


# --- the first synchronisation -------------------------------------------


def test_a_first_sync_indexes_every_discovered_file(db_session: Session) -> None:
    client = _client([])
    first = _with_content(client, _file("i1", "a.txt"), "The first document.")
    second = _with_content(client, _file("i2", "b.txt"), "The second document.")
    client.delta_pages = [([first, second], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.mode == "full"
    assert summary.discovered == 2
    assert summary.indexed == 2
    assert summary.status is SyncStatus.SUCCEEDED
    assert len(_documents(db_session)) == 2


def test_folders_are_not_counted_as_files(db_session: Session) -> None:
    client = _client([])
    folder = _file("f1", "Subfolder", folder=True)
    document = _with_content(client, _file("i1", "a.txt"), "Body.")
    client.delta_pages = [([folder, document], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.discovered == 1
    assert summary.indexed == 1


def test_the_delta_token_is_stored_for_next_time(db_session: Session) -> None:
    client = _client([])
    client.delta_pages = [
        ([_with_content(client, _file("i1", "a.txt"), "Body.")], "delta:token-1")
    ]

    sync_source(db_session, client, SOURCE)

    state = get_state(db_session, SOURCE.key)

    assert state is not None
    assert state.delta_link == "delta:token-1"
    assert state.last_succeeded_at is not None


def test_the_resolved_drive_and_item_are_remembered(db_session: Session) -> None:
    """Resolved once, then reused: a path is only correct until somebody
    renames a parent folder, and re-resolving would follow that rename."""

    client = _client([([], "delta:token-1")])

    sync_source(db_session, client, SOURCE)
    state = get_state(db_session, SOURCE.key)

    assert state is not None
    assert state.drive_id == DRIVE_ID
    assert state.item_id


def test_source_uri_and_version_reach_the_document(db_session: Session) -> None:
    """The identity the whole component turns on. Without it a second sync
    cannot tell a known file from a new one."""

    client = _client([])
    item = _with_content(client, _file("i1", "a.txt", version="ctag-1"), "Body.")
    client.delta_pages = [([item], "delta:token-1")]

    sync_source(db_session, client, SOURCE)

    document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")

    assert document is not None
    assert document.source_version == "ctag-1"
    assert document.status is DocumentStatus.INDEXED


# --- the second synchronisation ------------------------------------------


def test_an_unchanged_file_is_not_re_indexed(db_session: Session) -> None:
    client = _client([])
    item = _with_content(client, _file("i1", "a.txt"), "Body.")
    client.delta_pages = [([item], "delta:token-1"), ([item], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE)

    assert second.mode == "incremental"
    assert second.unchanged == 1
    assert second.indexed == 0
    assert len(_documents(db_session)) == 1


def test_a_sync_with_nothing_changed_is_a_no_op(db_session: Session) -> None:
    client = _client([])
    client.delta_pages = [
        ([_with_content(client, _file("i1", "a.txt"), "Body.")], "delta:token-1"),
        ([], "delta:token-2"),
    ]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE)

    assert second.discovered == 0
    assert second.status is SyncStatus.SUCCEEDED
    assert len(_documents(db_session)) == 1


def test_a_modified_file_replaces_the_indexed_one(db_session: Session) -> None:
    client = _client([])
    original = _with_content(
        client, _file("i1", "a.txt", version="ctag-1"), "Original."
    )
    changed = _file("i1", "a.txt", version="ctag-2")
    client.downloads[changed["@microsoft.graph.downloadUrl"]] = build_text(
        "Revised, and quite different."
    )
    client.delta_pages = [([original], "delta:token-1"), ([changed], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE)

    assert second.replaced == 1
    assert len(_documents(db_session)) == 1

    document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")
    assert document is not None
    assert document.source_version == "ctag-2"


# --- version and identity, pinned ----------------------------------------
#
# These four cover the identity contract the whole component rests on. They
# exist as a group because the failure that produced them was not in the
# service at all: the Graph double keyed a file's download URL on its item id
# alone, so registering version two's bytes overwrote version one's, and the
# *first* sync downloaded content that was supposed to arrive only in the
# second. The re-index worked; the fixture never presented it with a change.


def test_two_versions_of_a_file_are_offered_as_different_bytes() -> None:
    """The fixture property that, when it was missing, disguised a working
    re-index as a no-op. Graph reissues a pre-authorised URL per request, so
    two versions never share one — and a double that shares one cannot express
    "this file changed" at all."""

    first = _file("i1", "a.txt", version="ctag-1")
    second = _file("i1", "a.txt", version="ctag-2")

    assert (
        first["@microsoft.graph.downloadUrl"] != second["@microsoft.graph.downloadUrl"]
    )


def test_same_source_same_version_is_unchanged(db_session: Session) -> None:
    """Graph reporting a file it has already reported, with nothing altered."""

    client = _client([])
    item = _with_content(client, _file("i1", "a.txt", version="ctag-1"), "Body.")
    client.delta_pages = [([item], "delta:token-1"), ([item], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE)

    assert second.unchanged == 1
    assert second.replaced == 0
    assert len(_documents(db_session)) == 1


def test_same_source_new_version_and_new_content_is_replaced(
    db_session: Session,
) -> None:
    """The scenario the original failure was reaching for, stated directly."""

    client = _client([])
    original = _with_content(
        client, _file("i1", "a.txt", version="ctag-1"), "The original body."
    )
    revised = _with_content(
        client, _file("i1", "a.txt", version="ctag-2"), "A wholly different body."
    )
    client.delta_pages = [([original], "delta:token-1"), ([revised], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE)

    document = find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1")

    assert second.replaced == 1
    assert second.unchanged == 0
    assert len(_documents(db_session)) == 1
    assert document is not None
    assert document.source_version == "ctag-2"


def test_the_previous_version_stops_being_retrievable(
    db_session: Session, embed_query_as
) -> None:
    """Row counts are not the point — what the knowledge base *answers with*
    is. This runs the same `retrieval_service.search` that `/search` and
    `/chat` call, against the text the old version used to hold."""

    from app.services.features.retrieval.retrieval_service import search
    from tests.support.embeddings import deterministic_vector

    superseded = "The superseded statement about municipal water treatment."
    current = "The current statement about railway signalling instead."

    client = _client([])
    original = _with_content(client, _file("i1", "a.txt", version="ctag-1"), superseded)
    revised = _with_content(client, _file("i1", "a.txt", version="ctag-2"), current)
    client.delta_pages = [([original], "delta:token-1"), ([revised], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    sync_source(db_session, client, SOURCE)

    embed_query_as(deterministic_vector(superseded))
    results = search(db_session, "any question", similarity_threshold=None)

    assert all(superseded not in result.content for result in results)
    assert any(current in result.content for result in results)


def test_two_graph_items_are_two_documents(db_session: Session) -> None:
    """Different sources stay separate even when they share a filename — the
    corpus is full of files called `Capability Statement.pdf`."""

    client = _client([])
    first = _with_content(
        client, _file("i1", "Capability Statement.pdf"), "Statement for client A."
    )
    second = _with_content(
        client, _file("i2", "Capability Statement.pdf"), "Statement for client B."
    )
    client.delta_pages = [([first, second], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.indexed == 2
    assert len(_documents(db_session)) == 2
    assert find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1") is not None
    assert find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i2") is not None


def test_a_renamed_file_is_not_a_second_document(db_session: Session) -> None:
    """Identity is the Graph item id, so a rename is a metadata change to a
    document that already exists — not a new one."""

    client = _client([])
    original = _with_content(client, _file("i1", "a.txt"), "Body.")
    renamed = _file("i1", "renamed.txt")
    client.downloads[renamed["@microsoft.graph.downloadUrl"]] = build_text("Body.")
    client.delta_pages = [([original], "delta:token-1"), ([renamed], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    sync_source(db_session, client, SOURCE)

    assert len(_documents(db_session)) == 1


def test_a_deleted_file_removes_its_document(db_session: Session) -> None:
    client = _client([])
    item = _with_content(client, _file("i1", "a.txt"), "Body.")
    tombstone = _file("i1", "a.txt", deleted=True)
    client.delta_pages = [([item], "delta:token-1"), ([tombstone], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE)

    assert second.deleted == 1
    assert _documents(db_session) == []
    assert find_document_by_source(db_session, f"onedrive:{DRIVE_ID}:i1") is None


def test_deleting_something_never_indexed_is_not_a_failure(db_session: Session) -> None:
    """A file added and removed between two runs, or one whose format was
    never supported. Ordinary, not a fault."""

    client = _client([([_file("i9", "gone.txt", deleted=True)], "delta:token-1")])

    summary = sync_source(db_session, client, SOURCE)

    assert summary.deleted == 1
    assert summary.failed == 0
    assert summary.status is SyncStatus.SUCCEEDED


# --- files this system cannot take ---------------------------------------


def test_an_unsupported_format_is_reported_not_dropped(db_session: Session) -> None:
    client = _client([])
    item = _file("i1", "legacy.doc", mime_type="application/msword")
    client.downloads[item["@microsoft.graph.downloadUrl"]] = b"legacy binary"
    client.delta_pages = [([item], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.unsupported == 1
    assert summary.outcomes[0].result is SyncResult.UNSUPPORTED
    assert summary.outcomes[0].reason


def test_an_oversized_file_is_skipped_without_downloading_it(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size is known from metadata, so paying to transfer a file that will
    be rejected on arrival is avoidable."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_MAX_FILE_BYTES", 10)

    client = _client([])
    item = _with_content(client, _file("i1", "big.txt", size=5_000), "Body.")
    client.delta_pages = [([item], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.unsupported == 1
    assert client.downloaded == []


def test_an_unreadable_file_is_recorded_as_failed(db_session: Session) -> None:
    client = _client([])
    item = _file("i1", "broken.pdf", mime_type="application/pdf")
    client.downloads[item["@microsoft.graph.downloadUrl"]] = b"not a pdf at all"
    client.delta_pages = [([item], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.failed == 1
    assert summary.outcomes[0].document_id is not None


# --- isolation and resumability ------------------------------------------


def test_one_files_failure_does_not_stop_the_others(db_session: Session) -> None:
    client = _client([])
    good = _with_content(client, _file("i1", "a.txt"), "Readable.")
    broken = _file("i2", "broken.pdf", mime_type="application/pdf")
    client.downloads[broken["@microsoft.graph.downloadUrl"]] = b"not a pdf"
    later = _with_content(client, _file("i3", "c.txt"), "Also readable.")
    client.delta_pages = [([good, broken, later], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.indexed == 2
    assert summary.failed == 1
    assert len(_documents(db_session)) == 3


def test_a_failed_download_holds_the_delta_token_back(db_session: Session) -> None:
    """The rule the whole design turns on. A file whose bytes were never seen
    has not been dealt with, and a token claiming otherwise would bury it
    until it happened to change again."""

    client = _client([])
    good = _with_content(client, _file("i1", "a.txt"), "Readable.")
    missing = _file("i2", "gone.txt")
    client.fail_downloads.add(missing["@microsoft.graph.downloadUrl"])
    client.delta_pages = [([good, missing], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.failed == 1
    assert summary.delta_advanced is False
    assert summary.status is SyncStatus.PARTIAL

    state = get_state(db_session, SOURCE.key)
    assert state is not None
    assert state.delta_link is None


def test_the_files_that_did_land_are_kept_after_a_partial_run(
    db_session: Session,
) -> None:
    """Partial does not mean rolled back. The work that succeeded is real, and
    re-examining it next run is a skip, not a duplicate."""

    client = _client([])
    good = _with_content(client, _file("i1", "a.txt"), "Readable.")
    missing = _file("i2", "gone.txt")
    client.fail_downloads.add(missing["@microsoft.graph.downloadUrl"])
    client.delta_pages = [([good, missing], "delta:token-1")]

    sync_source(db_session, client, SOURCE)

    assert len(_documents(db_session)) == 1


def test_a_parse_failure_does_not_hold_the_token_back(db_session: Session) -> None:
    """A corrupt PDF will be just as corrupt next time. Withholding the token
    for it would freeze the source forever."""

    client = _client([])
    broken = _file("i1", "broken.pdf", mime_type="application/pdf")
    client.downloads[broken["@microsoft.graph.downloadUrl"]] = b"not a pdf"
    client.delta_pages = [([broken], "delta:token-1")]

    summary = sync_source(db_session, client, SOURCE)

    assert summary.failed == 1
    assert summary.delta_advanced is True


def test_a_graph_failure_before_any_work_leaves_state_untouched(
    db_session: Session,
) -> None:
    """Nothing was processed, so the previous good token is still true."""

    client = _client([])
    client.delta_pages = [
        ([_with_content(client, _file("i1", "a.txt"), "Body.")], "delta:token-1")
    ]
    sync_source(db_session, client, SOURCE)

    class Exploding(FakeGraphClient):
        def delta(self, path_or_url: str):
            from app.core.exceptions import GraphRequestError

            raise GraphRequestError("Graph request failed (HTTP 503).")

    summary = sync_source(db_session, Exploding(), SOURCE)

    assert summary.status is SyncStatus.FAILED
    assert summary.error

    state = get_state(db_session, SOURCE.key)
    assert state is not None
    assert state.delta_link == "delta:token-1"


# --- delta token expiry --------------------------------------------------


def test_an_expired_delta_token_falls_back_to_a_full_sync(
    db_session: Session,
) -> None:
    client = _client([])
    item = _with_content(client, _file("i1", "a.txt"), "Body.")
    client.delta_pages = [([item], "delta:token-1"), ([item], "delta:token-2")]

    sync_source(db_session, client, SOURCE)

    client.expire_delta_on.add(1)
    second = sync_source(db_session, client, SOURCE)

    assert second.mode == "full"
    assert second.status is SyncStatus.SUCCEEDED


def test_a_full_resync_after_expiry_does_not_duplicate_the_corpus(
    db_session: Session,
) -> None:
    """The reason the fallback is safe at all: re-enumerating costs time, not
    correctness, because unchanged content is skipped."""

    client = _client([])
    item = _with_content(client, _file("i1", "a.txt"), "Body.")
    client.delta_pages = [([item], "delta:token-1"), ([item], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    client.expire_delta_on.add(1)
    sync_source(db_session, client, SOURCE)

    assert len(_documents(db_session)) == 1


def test_a_forced_full_sync_ignores_the_stored_token(db_session: Session) -> None:
    client = _client([])
    item = _with_content(client, _file("i1", "a.txt"), "Body.")
    client.delta_pages = [([item], "delta:token-1"), ([item], "delta:token-2")]

    sync_source(db_session, client, SOURCE)
    second = sync_source(db_session, client, SOURCE, full=True)

    assert second.mode == "full"
    assert client.delta_calls[-1].endswith("/delta")


# --- temporary files -----------------------------------------------------


def test_nothing_is_left_behind_after_a_successful_sync(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """OneDrive is not mirrored. A staging directory that fills up is a mirror
    nobody decided to build."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_STAGING_DIR", str(tmp_path))

    client = _client([])
    client.delta_pages = [
        ([_with_content(client, _file("i1", "a.txt"), "Body.")], "delta:token-1")
    ]

    sync_source(db_session, client, SOURCE)

    assert os.listdir(tmp_path) == []


def test_nothing_is_left_behind_when_ingestion_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_STAGING_DIR", str(tmp_path))

    client = _client([])
    broken = _file("i1", "broken.pdf", mime_type="application/pdf")
    client.downloads[broken["@microsoft.graph.downloadUrl"]] = b"not a pdf"
    client.delta_pages = [([broken], "delta:token-1")]

    sync_source(db_session, client, SOURCE)

    assert os.listdir(tmp_path) == []


# --- configuration -------------------------------------------------------


def test_syncing_with_no_sources_configured_says_so(db_session: Session) -> None:
    with pytest.raises(SyncNotConfiguredError):
        sync_all(db_session, client=_client([]))


def test_an_unknown_source_key_is_named(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "ONEDRIVE_SOURCES",
        sources_json({"key": "capabilities", "path": "C", "drive_id": DRIVE_ID}),
    )

    with pytest.raises(SyncSourceNotFoundError) as error:
        sync_all(db_session, client=_client([]), source_key="nope")

    assert "capabilities" in str(error.value)


def test_every_configured_source_is_synchronised(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "ONEDRIVE_SOURCES",
        sources_json(
            {"key": "one", "path": "One", "drive_id": DRIVE_ID},
            {"key": "two", "path": "Two", "drive_id": DRIVE_ID},
        ),
    )

    client = _client([([], "delta:a"), ([], "delta:b")])

    summaries = sync_all(db_session, client=client)

    assert [summary.source_key for summary in summaries] == ["one", "two"]


def test_each_source_keeps_its_own_delta_token(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared state between sources would mean one folder's progress silently
    marking another folder as up to date."""

    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "ONEDRIVE_SOURCES",
        sources_json(
            {"key": "one", "path": "One", "drive_id": DRIVE_ID},
            {"key": "two", "path": "Two", "drive_id": DRIVE_ID},
        ),
    )

    sync_all(db_session, client=_client([([], "delta:a"), ([], "delta:b")]))

    first = get_state(db_session, "one")
    second = get_state(db_session, "two")

    assert first is not None and second is not None
    assert first.delta_link == "delta:a"
    assert second.delta_link == "delta:b"


# --- one run at a time, per source ---------------------------------------
#
# The scheduler and a manual API call can both reach `sync_source`. Two runs
# over one source would download the same files twice and race to write the
# same delta token — and the loser's token would describe work the winner
# never did. The claim is made in the database because the two callers may not
# even be the same process.


def test_a_source_already_running_is_refused(db_session: Session) -> None:
    client = _client([([], "delta:token-1")])
    sync_source(db_session, client, SOURCE)

    state = get_state(db_session, SOURCE.key)
    assert state is not None
    state.status = SyncStatus.RUNNING
    state.last_attempted_at = datetime.now(UTC)
    db_session.commit()

    with pytest.raises(SyncAlreadyRunningError):
        sync_source(db_session, client, SOURCE)


def test_a_refused_run_leaves_the_delta_token_alone(db_session: Session) -> None:
    """The run that is genuinely in flight owns the token. A second caller must
    not touch it on its way out."""

    client = _client([([], "delta:token-1")])
    sync_source(db_session, client, SOURCE)

    state = get_state(db_session, SOURCE.key)
    assert state is not None
    state.status = SyncStatus.RUNNING
    state.last_attempted_at = datetime.now(UTC)
    db_session.commit()

    with pytest.raises(SyncAlreadyRunningError):
        sync_source(db_session, client, SOURCE)

    db_session.refresh(state)
    assert state.delta_link == "delta:token-1"


def test_a_stale_running_flag_does_not_block_forever(db_session: Session) -> None:
    """A process killed mid-sync leaves RUNNING behind. Refusing every future
    sync because of a crash would be worse than the double-run the flag exists
    to prevent."""

    client = _client([([], "delta:token-1"), ([], "delta:token-2")])
    sync_source(db_session, client, SOURCE)

    state = get_state(db_session, SOURCE.key)
    assert state is not None
    state.status = SyncStatus.RUNNING
    state.last_attempted_at = datetime.now(UTC) - timedelta(hours=12)
    db_session.commit()

    summary = sync_source(db_session, client, SOURCE)

    assert summary.status is SyncStatus.SUCCEEDED


def test_a_busy_source_does_not_stop_the_others(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five folders are configured. One of them being mid-run is not a reason
    for the other four to sit idle."""

    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "ONEDRIVE_SOURCES",
        sources_json(
            {"key": "one", "path": "One", "drive_id": DRIVE_ID},
            {"key": "two", "path": "Two", "drive_id": DRIVE_ID},
        ),
    )

    client = _client([([], "delta:a"), ([], "delta:b")])
    sync_all(db_session, client=client)

    busy = get_state(db_session, "one")
    assert busy is not None
    busy.status = SyncStatus.RUNNING
    busy.last_attempted_at = datetime.now(UTC)
    db_session.commit()

    summaries = sync_all(db_session, client=_client([([], "delta:c")]))

    by_key = {summary.source_key: summary for summary in summaries}

    assert by_key["one"].mode == "skipped"
    assert by_key["one"].error
    assert by_key["two"].status is SyncStatus.SUCCEEDED


# --- enabled and disabled sources ----------------------------------------


def _configure(monkeypatch: pytest.MonkeyPatch, *entries: dict) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "ONEDRIVE_SOURCES", sources_json(*entries)
    )


def test_a_disabled_source_is_not_synchronised(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(
        monkeypatch,
        {"key": "on", "path": "One", "drive_id": DRIVE_ID},
        {"key": "off", "path": "Two", "drive_id": DRIVE_ID, "enabled": False},
    )

    summaries = sync_all(db_session, client=_client([([], "delta:a")]))

    assert [summary.source_key for summary in summaries] == ["on"]
    # No state row either: a source that has never run has nothing to record.
    assert get_state(db_session, "off") is None


def test_disabling_a_source_keeps_its_delta_token(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabling is a pause, not a deletion. Re-enabling must resume rather
    than re-download and re-embed the whole folder."""

    _configure(monkeypatch, {"key": "off", "path": "Two", "drive_id": DRIVE_ID})
    sync_all(db_session, client=_client([([], "delta:kept")]))

    _configure(
        monkeypatch,
        {"key": "off", "path": "Two", "drive_id": DRIVE_ID, "enabled": False},
    )
    # With the only source disabled there is nothing to run, which is the same
    # state as an unconfigured deployment.
    with pytest.raises(SyncNotConfiguredError):
        sync_all(db_session, client=_client([([], "delta:ignored")]))

    state = get_state(db_session, "off")

    assert state is not None
    assert state.delta_link == "delta:kept"


def test_naming_a_disabled_source_is_refused_rather_than_obeyed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Syncing it anyway would contradict the configuration the deployment is
    running, which is a surprise rather than an override."""

    _configure(
        monkeypatch,
        {"key": "off", "path": "Two", "drive_id": DRIVE_ID, "enabled": False},
    )

    with pytest.raises(SyncNotConfiguredError) as error:
        sync_all(db_session, client=_client([]), source_key="off")

    assert "off" in str(error.value)


def test_every_source_being_disabled_reads_as_nothing_configured(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(
        monkeypatch,
        {"key": "off", "path": "Two", "drive_id": DRIVE_ID, "enabled": False},
    )

    with pytest.raises(SyncNotConfiguredError):
        sync_all(db_session, client=_client([]))


# --- isolation from the unexpected ---------------------------------------


class _Exploding:
    """A client that fails in a way nobody wrote a handler for."""

    def get(self, *_args, **_kwargs) -> dict:
        raise RuntimeError("socket exploded")

    def delta(self, *_args, **_kwargs):  # pragma: no cover - never reached
        raise RuntimeError("socket exploded")

    def paged(self, *_args, **_kwargs):  # pragma: no cover - never reached
        raise RuntimeError("socket exploded")

    def download(self, *_args, **_kwargs):  # pragma: no cover - never reached
        raise RuntimeError("socket exploded")


def test_an_unexpected_failure_does_not_leave_a_source_stuck_running(
    db_session: Session,
) -> None:
    """The row is marked `running` before Graph is contacted. If an unforeseen
    error escaped, that flag would block every future run of this source for
    six hours — over a bug, not over a real concurrent run."""

    summary = sync_source(db_session, _Exploding(), SOURCE)  # type: ignore[arg-type]

    assert summary.status is SyncStatus.FAILED

    state = get_state(db_session, SOURCE.key)

    assert state is not None
    assert state.status is SyncStatus.FAILED


def test_an_unexpected_failure_is_reported_without_its_internals(
    db_session: Session,
) -> None:
    """Stack traces and driver messages are for the log, not for a response."""

    summary = sync_source(db_session, _Exploding(), SOURCE)  # type: ignore[arg-type]

    assert "socket" not in (summary.error or "")
    assert "RuntimeError" in (summary.error or "")


def test_one_source_exploding_does_not_stop_the_others(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(
        monkeypatch,
        {"key": "broken", "path": "One", "drive_id": DRIVE_ID},
        {"key": "fine", "path": "Two", "drive_id": DRIVE_ID},
    )

    working = _client([([], "delta:a")])

    class _BrokenOnce:
        """Fails for the first source and behaves for the second."""

        def __init__(self) -> None:
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("socket exploded")
            return working.get(*args, **kwargs)

        def delta(self, *args, **kwargs):
            return working.delta(*args, **kwargs)

        def paged(self, *args, **kwargs):
            return working.paged(*args, **kwargs)

        def download(self, *args, **kwargs):
            return working.download(*args, **kwargs)

    summaries = sync_all(db_session, client=_BrokenOnce())  # type: ignore[arg-type]
    by_key = {summary.source_key: summary for summary in summaries}

    assert by_key["broken"].status is SyncStatus.FAILED
    assert by_key["fine"].status is SyncStatus.SUCCEEDED


def test_a_file_reached_by_two_sources_is_one_document(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Case Study` sits inside `Capabilities`, so the two configured folders
    overlap. Identity is the Graph item, not the folder that found it, so the
    second source sees an already-indexed file rather than making a copy."""

    _configure(
        monkeypatch,
        {"key": "capabilities", "path": "Capabilities", "drive_id": DRIVE_ID},
        {"key": "case-study", "path": "Capabilities/Case Study", "drive_id": DRIVE_ID},
    )

    client = _client([])
    shared = _with_content(client, _file("shared-1", "study.txt"), "A shared study.")
    client.delta_pages = [([shared], "delta:a"), ([shared], "delta:b")]

    summaries = sync_all(db_session, client=client)
    by_key = {summary.source_key: summary for summary in summaries}

    assert by_key["capabilities"].indexed == 1
    assert by_key["case-study"].unchanged == 1
    assert len(_documents(db_session)) == 1


# --- sources named by a sharing link -------------------------------------


def test_a_share_linked_source_syncs_without_being_pinned_first(
    db_session: Session,
) -> None:
    """A folder shared from elsewhere has a drive id nobody has written down.

    Resolution runs on the sync path, so configuring the link is enough: no
    separate pinning step stands between a configured source and its first
    run.
    """

    source = OneDriveSource(
        key="capabilities",
        label="Capabilities",
        share_url="https://contoso.sharepoint.com/:f:/s/KB/AbC",
    )

    client = _client([])
    client.shared = {
        "id": "shared-1",
        "name": "Capabilities",
        "folder": {"childCount": 1},
        "parentReference": {"driveId": "library-drive-1"},
    }
    item = _with_content(client, _file("i1", "a.txt"), "A shared document.")
    client.delta_pages = [([item], "delta:1")]

    summary = sync_source(db_session, client, source)

    assert summary.status is SyncStatus.SUCCEEDED
    assert summary.indexed == 1


def test_the_drive_graph_named_is_what_gets_remembered(db_session: Session) -> None:
    """The shared folder's real drive, not the one anybody configured."""

    source = OneDriveSource(
        key="capabilities",
        label="Capabilities",
        share_url="https://contoso.sharepoint.com/:f:/s/KB/AbC",
    )

    client = _client([([], "delta:1")])
    client.shared = {
        "id": "shared-1",
        "name": "Capabilities",
        "folder": {},
        "parentReference": {"driveId": "library-drive-1"},
    }

    sync_source(db_session, client, source)

    state = get_state(db_session, "capabilities")

    assert state is not None
    assert (state.drive_id, state.item_id) == ("library-drive-1", "shared-1")


def test_a_source_that_cannot_be_resolved_says_what_would_fix_it(
    db_session: Session,
) -> None:
    """The message a person reads when a sync fails has to carry the remedy.

    "Access denied" alone sends somebody granting permissions; naming the
    reason is what stops that.
    """

    source = OneDriveSource(
        key="consumer",
        label="Consumer folder",
        share_url="https://onedrive.live.com/?id=root&cid=ABC",
    )

    summary = sync_source(db_session, _client([]), source)

    assert summary.status is SyncStatus.FAILED
    assert "personal Microsoft account" in (summary.error or "")
    assert "copied or moved" in (summary.error or "")

    state = get_state(db_session, "consumer")

    assert state is not None
    # Released rather than left claiming to be running.
    assert state.status is SyncStatus.FAILED
