"""Reading a OneDrive folder as a flat list of files.

Everything here returns `DriveItem` — a plain frozen dataclass — so that the
sync service never handles a raw Graph payload. When Graph changes the shape
of a response, this file changes and nothing above it does.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from app.services.graph import share_link
from app.services.graph.client import GraphClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriveItem:
    """One file or folder in a drive, reduced to what ingestion needs."""

    item_id: str
    name: str
    drive_id: str
    is_folder: bool
    deleted: bool
    size: int | None = None
    # Graph's cTag changes when *content* changes; eTag also changes on a
    # metadata edit. Content is the question being asked, so cTag is the
    # version recorded — a file whose description was edited should not be
    # re-downloaded, re-parsed and re-embedded.
    version: str | None = None
    download_url: str | None = None
    mime_type: str | None = None
    parent_path: str | None = None

    @property
    def source_uri(self) -> str:
        """The stable identity this file carries into the knowledge base.

        Drive and item id, never the name or the path: a file that is renamed
        or moved between folders is the same document, and must re-index in
        place rather than arrive as a second copy of itself.
        """

        return f"onedrive:{self.drive_id}:{self.item_id}"


def _parse_item(payload: dict, *, drive_id: str) -> DriveItem:
    # A "shortcut to a shared folder" is a stub in one drive standing for an
    # item in another. Its own id addresses the stub, which has no children and
    # no delta of its own, so a sync pointed at it would find an empty folder
    # and report success. `remoteItem` carries the real drive and item, and
    # taking them is the difference between reading the shared folder and
    # reading a pointer to it.
    remote = payload.get("remoteItem")

    if isinstance(remote, dict) and remote:
        payload = {
            **payload,
            **remote,
            # The stub's name is what a person sees in their own drive, and is
            # the better label; everything else comes from the target.
            "name": payload.get("name") or remote.get("name"),
        }

    parent = payload.get("parentReference") or {}
    file_facet = payload.get("file") or {}

    return DriveItem(
        item_id=str(payload.get("id", "")),
        name=str(payload.get("name", "")),
        # A delta payload names the drive on each item; a children listing may
        # not, so the caller's drive is the fallback.
        drive_id=str(parent.get("driveId") or drive_id),
        is_folder="folder" in payload,
        deleted="deleted" in payload,
        size=payload.get("size"),
        version=payload.get("cTag") or payload.get("eTag"),
        download_url=payload.get("@microsoft.graph.downloadUrl"),
        mime_type=file_facet.get("mimeType"),
        parent_path=parent.get("path"),
    )


def resolve_folder(
    client: GraphClient,
    *,
    drive_id: str | None,
    path: str | None = None,
    item_id: str | None = None,
) -> DriveItem:
    """Find a configured folder, by id if known and by path otherwise.

    Resolving by id is preferred and is why the result is worth persisting:
    a path is a description of where a folder sits today, and someone renaming
    a parent silently redirects every future sync.
    """

    if not drive_id:
        raise ValueError("A drive id is required to resolve a OneDrive folder.")

    if item_id:
        payload = client.get(f"/drives/{drive_id}/items/{item_id}")
    elif path:
        # Graph addresses a path-based item as `root:/a/b/c:`. Leading and
        # trailing slashes produce an empty segment and a 400.
        cleaned = path.strip("/")
        payload = client.get(f"/drives/{drive_id}/root:/{cleaned}:")
    else:
        payload = client.get(f"/drives/{drive_id}/root")

    return _parse_item(payload, drive_id=drive_id)


def resolve_shared_item(client: GraphClient, share_url: str) -> DriveItem:
    """Ask Graph which drive item a sharing link stands for.

    `/shares/{token}/driveItem` is the documented way to turn a sharing URL
    into a real item. The token is the whole URL, encoded — nothing about the
    link is interpreted here, and the `driveId` and `id` in the result are read
    from Graph's answer. That is what makes this safe where decoding the id
    that appears inside a sharing URL is not.

    `$select` names `parentReference` explicitly because the drive id lives
    there, and a shared item's drive is routinely not the drive anybody
    configured.
    """

    token = share_link.sharing_token(share_url)

    payload = client.get(
        f"/shares/{token}/driveItem",
        params={"$select": "id,name,size,cTag,eTag,file,folder,parentReference"},
    )

    parent = payload.get("parentReference") or {}

    return _parse_item(payload, drive_id=str(parent.get("driveId") or ""))


def search_folder(
    client: GraphClient, *, drive_id: str, query: str, item_id: str | None = None
) -> list[DriveItem]:
    """Find items by name within a drive, or beneath one folder in it.

    Exists for the case a configured path returns `itemNotFound`: the folder
    may be real and one level deeper than anybody wrote down, and searching is
    how that is settled in one call rather than by guessing at prefixes.
    """

    root = (
        f"/drives/{drive_id}/items/{item_id}" if item_id else f"/drives/{drive_id}/root"
    )
    escaped = query.replace("'", "''")

    return [
        _parse_item(payload, drive_id=drive_id)
        for payload in client.paged(f"{root}/search(q='{escaped}')")
    ]


def iter_files(
    client: GraphClient, *, drive_id: str, item_id: str
) -> Iterator[DriveItem]:
    """Yield every file beneath a folder, descending into subfolders.

    Breadth-first over an explicit stack rather than recursion: a deep tree
    should not be able to exhaust the interpreter's stack, and the traversal
    order is not something callers should depend on anyway.
    """

    pending = [item_id]
    seen: set[str] = set()

    while pending:
        current = pending.pop()

        if current in seen:
            # A drive should be a tree, but a malformed or shortcut-laden one
            # need not be, and an infinite walk is a worse failure than a
            # missing file.
            continue
        seen.add(current)

        for payload in client.paged(f"/drives/{drive_id}/items/{current}/children"):
            item = _parse_item(payload, drive_id=drive_id)

            if item.deleted:
                continue

            if item.is_folder:
                pending.append(item.item_id)
                continue

            yield item


def iter_delta(
    client: GraphClient,
    *,
    drive_id: str,
    item_id: str,
    delta_link: str | None = None,
) -> tuple[list[DriveItem], str | None]:
    """Return what changed since `delta_link`, plus the link for next time.

    With no link, this is a full enumeration that also establishes a starting
    point — which is why the initial sync and the incremental one can share a
    code path instead of being two implementations of the same idea.

    Raises `DeltaTokenExpiredError` if Graph refuses the token; the caller is
    expected to retry with `delta_link=None`.
    """

    url = delta_link or f"/drives/{drive_id}/items/{item_id}/delta"
    payloads, next_delta_link = client.delta(url)

    items = [_parse_item(payload, drive_id=drive_id) for payload in payloads]

    # The root of the traversal comes back as a changed item on a full delta.
    # It is not a file and it is not news.
    changes = [item for item in items if item.item_id != item_id]

    logger.info(
        "graph_delta_read",
        extra={
            "drive_id": drive_id,
            "item_id": item_id,
            "changes": len(changes),
            "incremental": bool(delta_link),
        },
    )

    return changes, next_delta_link
