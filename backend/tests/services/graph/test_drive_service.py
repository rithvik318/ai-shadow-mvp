"""Reading a drive as a flat list of files.

These assertions are about the translation from Graph's payload shape into
`DriveItem`. Everything above this layer depends on that translation being
right and has no way to notice when it is not.
"""

import pytest

from app.services.graph.client import GraphClient
from app.services.graph.drive_service import (
    iter_delta,
    iter_files,
    resolve_folder,
    resolve_shared_item,
    search_folder,
)
from tests.support.graph import DRIVE_ID, drive_item, graph_transport, shortcut_item


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


# --- shortcuts to shared folders -----------------------------------------


def test_a_shortcut_resolves_to_the_folder_it_points_at() -> None:
    """ "Add shortcut to My files" leaves a stub in the person's own drive.

    The stub has no children and no delta of its own, so a sync pointed at its
    id finds an empty folder and reports success — a silent, complete failure.
    """

    routes = {
        "/root/children": {
            "value": [
                shortcut_item(
                    "stub-1",
                    "Capabilities",
                    target_item_id="real-1",
                    target_drive_id="other-drive",
                )
            ]
        },
        "/items/real-1/children": {"value": []},
    }

    items = list(iter_files(_client(routes), drive_id=DRIVE_ID, item_id="root"))

    # It is a folder in the other drive, so it is descended into, not yielded.
    assert items == []


def test_a_shortcut_carries_the_targets_drive_and_item() -> None:
    routes = {
        "root:/Capabilities:": shortcut_item(
            "stub-1",
            "Capabilities",
            target_item_id="real-1",
            target_drive_id="other-drive",
        )
    }

    folder = resolve_folder(_client(routes), drive_id=DRIVE_ID, path="Capabilities")

    assert folder.item_id == "real-1"
    assert folder.drive_id == "other-drive"
    assert folder.is_folder


def test_a_shortcut_keeps_the_name_a_person_sees() -> None:
    routes = {
        "root:/Shared:": shortcut_item(
            "stub-1", "Shared", target_item_id="real-1", target_drive_id="other-drive"
        )
    }

    assert resolve_folder(_client(routes), drive_id=DRIVE_ID, path="Shared").name == (
        "Shared"
    )


def test_a_shortcuts_identity_is_the_targets_not_the_stubs() -> None:
    """Two people with shortcuts to one folder must not produce two documents
    per file, and neither must be keyed to a stub that can be deleted."""

    routes = {
        "root:/Shared:": shortcut_item(
            "stub-1",
            "Shared",
            target_item_id="real-1",
            target_drive_id="other-drive",
            folder=False,
        )
    }

    item = resolve_folder(_client(routes), drive_id=DRIVE_ID, path="Shared")

    assert item.source_uri == "onedrive:other-drive:real-1"


def test_an_ordinary_item_is_unaffected_by_shortcut_handling() -> None:
    routes = {"root:/Plain:": drive_item("plain-1", "Plain", folder=True)}

    folder = resolve_folder(_client(routes), drive_id=DRIVE_ID, path="Plain")

    assert folder.item_id == "plain-1"
    assert folder.drive_id == DRIVE_ID


# --- resolving a sharing link --------------------------------------------


def test_a_sharing_link_resolves_to_a_drive_and_item_graph_supplied() -> None:
    """The ids come out of Graph's response body. Nothing parses the URL."""

    routes = {
        "/shares/": {
            "id": "shared-item-1",
            "name": "Capabilities",
            "folder": {"childCount": 9},
            "parentReference": {"driveId": "library-drive-1"},
            "cTag": "c1",
        }
    }

    item = resolve_shared_item(
        _client(routes), "https://contoso.sharepoint.com/:f:/s/KB/AbC"
    )

    assert item.item_id == "shared-item-1"
    assert item.drive_id == "library-drive-1"
    assert item.is_folder
    assert item.source_uri == "onedrive:library-drive-1:shared-item-1"


def test_a_shared_item_whose_drive_differs_from_the_configured_one_wins() -> None:
    """A shared folder's drive is routinely not the drive anybody wrote down;
    keeping the configured one would address the wrong library."""

    routes = {
        "/shares/": {
            "id": "shared-1",
            "name": "Case Study",
            "folder": {},
            "parentReference": {"driveId": "somebody-elses-drive"},
        }
    }

    item = resolve_shared_item(
        _client(routes), "https://contoso-my.sharepoint.com/:f:/g/personal/x/Ab"
    )

    assert item.drive_id == "somebody-elses-drive"


def test_a_shared_link_that_resolves_to_a_shortcut_follows_it_too() -> None:
    routes = {
        "/shares/": shortcut_item(
            "stub-1",
            "Capabilities",
            target_item_id="real-1",
            target_drive_id="far-away",
        )
    }

    item = resolve_shared_item(
        _client(routes), "https://contoso.sharepoint.com/:f:/s/KB/AbC"
    )

    assert (item.drive_id, item.item_id) == ("far-away", "real-1")


# --- searching a drive ---------------------------------------------------


def test_a_folder_can_be_found_by_name_when_a_path_is_wrong() -> None:
    """`itemNotFound` on a path means either the folder is absent or it is one
    level deeper than somebody wrote down. Only a search tells them apart."""

    routes = {
        "/root/search": {
            "value": [
                drive_item("found-1", "Capabilities", folder=True),
                drive_item("found-2", "Capabilities archive", folder=True),
            ]
        }
    }

    found = search_folder(_client(routes), drive_id=DRIVE_ID, query="Capabilities")

    assert [item.name for item in found] == ["Capabilities", "Capabilities archive"]
    assert found[0].item_id == "found-1"


def test_a_quote_in_a_search_term_cannot_break_the_query() -> None:
    routes = {"/root/search": {"value": []}}

    assert search_folder(_client(routes), drive_id=DRIVE_ID, query="Bob's files") == []
