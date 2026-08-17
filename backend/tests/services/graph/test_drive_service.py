"""Reading a drive as a flat list of files.

These assertions are about the translation from Graph's payload shape into
`DriveItem`. Everything above this layer depends on that translation being
right and has no way to notice when it is not.
"""

import pytest

from app.services.graph.client import GraphClient
from app.services.graph.drive_service import iter_delta, iter_files, resolve_folder
from tests.support.graph import DRIVE_ID, drive_item, graph_transport


def _client(routes, **kwargs) -> GraphClient:
    return GraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        base_url="https://graph.example/v1.0",
        authority="https://login.example",
        transport=graph_transport(routes, **kwargs),
    )


# --- resolving a configured folder ---------------------------------------


def test_a_folder_is_resolved_by_path() -> None:
    routes = {
        "/root:/Capabilities:": drive_item("folder-1", "Capabilities", folder=True)
    }

    folder = resolve_folder(_client(routes), drive_id=DRIVE_ID, path="Capabilities")

    assert folder.item_id == "folder-1"
    assert folder.is_folder


def test_a_nested_path_is_addressed_correctly() -> None:
    """Graph addresses a path as `root:/a/b/c:`, and a stray slash produces an
    empty segment and a 400 rather than a helpful error."""

    seen: list[str] = []
    routes = {"/root:/": drive_item("folder-2", "Case Study", folder=True)}
    client = _client(routes)

    original = client.get

    def recording(path, params=None):
        seen.append(path)
        return original(path, params)

    client.get = recording  # type: ignore[method-assign]

    resolve_folder(
        client, drive_id=DRIVE_ID, path="/Capabilities/2024 and Earlier/Case Study/"
    )

    assert seen == [
        f"/drives/{DRIVE_ID}/root:/Capabilities/2024 and Earlier/Case Study:"
    ]


def test_a_folder_is_resolved_by_item_id_when_one_is_known() -> None:
    routes = {"/items/01ABC": drive_item("01ABC", "Freddie Mac 2026", folder=True)}

    folder = resolve_folder(_client(routes), drive_id=DRIVE_ID, item_id="01ABC")

    assert folder.item_id == "01ABC"


def test_resolving_without_a_drive_is_rejected() -> None:
    with pytest.raises(ValueError, match="drive id"):
        resolve_folder(_client({}), drive_id=None, path="Capabilities")


# --- walking the tree ----------------------------------------------------


def test_files_are_discovered_recursively() -> None:
    """The corpus is organised in nested folders, so a listing that stops at
    the top level would index a fraction of it."""

    routes = {
        "/items/root/children": {
            "value": [
                drive_item("f1", "Nested", folder=True),
                drive_item("i1", "top.txt"),
            ]
        },
        "/items/f1/children": {"value": [drive_item("i2", "nested.txt")]},
    }

    files = list(iter_files(_client(routes), drive_id=DRIVE_ID, item_id="root"))

    assert {file.name for file in files} == {"top.txt", "nested.txt"}


def test_folders_are_descended_but_not_yielded() -> None:
    routes = {
        "/items/root/children": {"value": [drive_item("f1", "Nested", folder=True)]},
        "/items/f1/children": {"value": [drive_item("i2", "nested.txt")]},
    }

    files = list(iter_files(_client(routes), drive_id=DRIVE_ID, item_id="root"))

    assert [file.name for file in files] == ["nested.txt"]
    assert not any(file.is_folder for file in files)


def test_a_cycle_does_not_walk_forever() -> None:
    """A drive should be a tree. A malformed one should still terminate."""

    routes = {
        "/items/root/children": {"value": [drive_item("f1", "A", folder=True)]},
        "/items/f1/children": {"value": [drive_item("root", "Back", folder=True)]},
    }

    files = list(iter_files(_client(routes), drive_id=DRIVE_ID, item_id="root"))

    assert files == []


def test_deleted_items_are_not_yielded_by_a_listing() -> None:
    routes = {
        "/items/root/children": {
            "value": [
                drive_item("i1", "gone.txt", deleted=True),
                drive_item("i2", "a.txt"),
            ]
        }
    }

    files = list(iter_files(_client(routes), drive_id=DRIVE_ID, item_id="root"))

    assert [file.name for file in files] == ["a.txt"]


# --- delta ---------------------------------------------------------------


def test_delta_returns_changes_and_the_next_link() -> None:
    routes = {
        "/delta": {
            "value": [drive_item("i1", "a.txt")],
            "@odata.deltaLink": "https://graph.example/delta?token=1",
        }
    }

    changes, link = iter_delta(_client(routes), drive_id=DRIVE_ID, item_id="root")

    assert [change.name for change in changes] == ["a.txt"]
    assert link == "https://graph.example/delta?token=1"


def test_the_root_of_the_traversal_is_not_reported_as_a_change() -> None:
    """A full delta includes the folder itself, which is neither a file nor
    news, and counting it would inflate every discovery total by one."""

    routes = {
        "/delta": {
            "value": [
                drive_item("root", "Capabilities", folder=True),
                drive_item("i1", "a.txt"),
            ],
            "@odata.deltaLink": "https://graph.example/delta?token=1",
        }
    }

    changes, _link = iter_delta(_client(routes), drive_id=DRIVE_ID, item_id="root")

    assert [change.item_id for change in changes] == ["i1"]


def test_a_stored_delta_link_is_used_verbatim() -> None:
    """The link is an opaque URL carrying Graph's own state. Rebuilding it
    from parts is how an incremental sync silently becomes a full one."""

    routes = {"token=stored": {"value": [], "@odata.deltaLink": "https://d/next"}}

    _changes, link = iter_delta(
        _client(routes),
        drive_id=DRIVE_ID,
        item_id="root",
        delta_link="https://graph.example/delta?token=stored",
    )

    assert link == "https://d/next"


def test_a_tombstone_is_carried_through_as_deleted() -> None:
    routes = {
        "/delta": {
            "value": [drive_item("i1", "gone.txt", deleted=True)],
            "@odata.deltaLink": "https://d/next",
        }
    }

    changes, _link = iter_delta(_client(routes), drive_id=DRIVE_ID, item_id="root")

    assert changes[0].deleted


# --- identity ------------------------------------------------------------


def test_source_uri_is_built_from_drive_and_item() -> None:
    """Not the name and not the path: a file that is renamed or moved is the
    same document and must re-index in place."""

    routes = {"/delta": {"value": [drive_item("i1", "a.txt")], "@odata.deltaLink": "x"}}

    changes, _link = iter_delta(_client(routes), drive_id=DRIVE_ID, item_id="root")

    assert changes[0].source_uri == f"onedrive:{DRIVE_ID}:i1"


def test_the_content_tag_is_preferred_as_the_version() -> None:
    """cTag changes when content changes; eTag also changes on a metadata
    edit. Using eTag would re-download and re-embed a file whose description
    somebody tidied."""

    payload = drive_item("i1", "a.txt", version="ctag-value")
    payload["eTag"] = "etag-value"
    routes = {"/delta": {"value": [payload], "@odata.deltaLink": "x"}}

    changes, _link = iter_delta(_client(routes), drive_id=DRIVE_ID, item_id="root")

    assert changes[0].version == "ctag-value"
