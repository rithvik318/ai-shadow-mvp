"""Synchronisation, with Graph replaced and the knowledge base real.

The database, ingestion, chunking and embedding are all the production ones —
only Graph is a double. That is deliberate: the questions worth asking here
are "did the right document end up in the corpus" and "is it safe to resume",
and neither can be answered by asserting which methods were called.
"""

import os

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import SyncNotConfiguredError, SyncSourceNotFoundError
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
