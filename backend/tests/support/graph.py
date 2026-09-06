"""A Microsoft Graph that lives entirely in the test process.

Two levels are offered, because the tests need different things.

`graph_transport` is an `httpx.MockTransport` handed to a real `GraphClient`,
so the client's own behaviour — token minting and caching, paging,
`@odata.nextLink` following, error translation — is exercised against the
code that ships rather than around it.

`FakeGraphClient` replaces the client entirely, for the sync service, whose
tests are about what happens to documents and delta tokens rather than about
HTTP. It records calls so a test can assert that an unchanged file was never
downloaded.

Neither reaches the network, and no test needs a credential.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.exceptions import DeltaTokenExpiredError, GraphRequestError

DRIVE_ID = "test-drive"
FOLDER_ID = "folder-root"

TOKEN_URL_FRAGMENT = "/oauth2/v2.0/token"


def drive_item(
    item_id: str,
    name: str,
    *,
    folder: bool = False,
    deleted: bool = False,
    size: int = 32,
    version: str = "v1",
    mime_type: str | None = "text/plain",
    download_url: str | None = None,
    drive_id: str = DRIVE_ID,
) -> dict:
    """Build a Graph driveItem payload the way Graph actually shapes one."""

    payload: dict[str, Any] = {
        "id": item_id,
        "name": name,
        "size": size,
        "cTag": version,
        "parentReference": {"driveId": drive_id, "path": f"/drive/root:/{name}"},
    }

    if folder:
        payload["folder"] = {"childCount": 0}
    else:
        payload["file"] = {"mimeType": mime_type}
        # Keyed on the version as well as the item. Graph's pre-authorised
        # download URLs are short-lived and reissued per request, so two
        # versions of one file never share one — and a double that *does*
        # share one is actively misleading: registering the new version's
        # bytes silently replaces the old version's, so the first sync in a
        # test downloads content that is supposed to arrive only in the
        # second. That is what made a working re-index look like a no-op.
        payload["@microsoft.graph.downloadUrl"] = (
            download_url or f"https://files.example/{item_id}?v={version}"
        )

    if deleted:
        payload["deleted"] = {"state": "deleted"}

    return payload


def shortcut_item(
    item_id: str,
    name: str,
    *,
    target_item_id: str,
    target_drive_id: str,
    folder: bool = True,
) -> dict:
    """A stub in one drive standing for an item in another.

    What "Add shortcut to My files" leaves behind. The outer id addresses the
    stub, which has no children of its own; the real drive and item are in
    `remoteItem`. A test that does not distinguish the two cannot notice a sync
    reading the pointer instead of the folder.
    """

    payload: dict[str, Any] = {
        "id": item_id,
        "name": name,
        "parentReference": {"driveId": DRIVE_ID},
        "remoteItem": {
            "id": target_item_id,
            "name": name,
            "size": 4096,
            "cTag": "remote-v1",
            "parentReference": {"driveId": target_drive_id},
        },
    }

    if folder:
        payload["remoteItem"]["folder"] = {"childCount": 3}
    else:
        payload["remoteItem"]["file"] = {"mimeType": "text/plain"}

    return payload


def graph_transport(
    routes: dict[str, Any],
    *,
    downloads: dict[str, bytes] | None = None,
    token_status: int = 200,
    redirects: dict[str, str] | None = None,
) -> httpx.MockTransport:
    """A transport that answers Graph URLs from a dict.

    Keys are matched as substrings of the request URL, longest first, so a
    test can register `/delta` without spelling out the whole absolute URL.
    Values are either a payload dict or an (status, payload) tuple.

    `redirects` answers a matching URL with a 302 to the given target, so a
    shortened sharing link can be followed the way the real one is. The target
    still has to be answerable, or the redirect lands on a 404.
    """

    downloads = downloads or {}
    redirects = redirects or {}
    ordered = sorted(routes, key=len, reverse=True)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        for prefix, target in redirects.items():
            if prefix in url:
                return httpx.Response(302, headers={"Location": target})

        if TOKEN_URL_FRAGMENT in url:
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_client"})

            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 3600}
            )

        for prefix, payload in downloads.items():
            if prefix in url:
                return httpx.Response(200, content=payload)

        for key in ordered:
            if key in url:
                value = routes[key]

                if isinstance(value, tuple):
                    status, body = value
                    return httpx.Response(status, json=body)

                return httpx.Response(200, json=value)

        return httpx.Response(404, json={"error": {"code": "itemNotFound"}})

    return httpx.MockTransport(handler)


@dataclass
class FakeGraphClient:
    """A `GraphClient` stand-in for tests about synchronisation, not HTTP.

    `delta_pages` is a list of (items, delta_link) tuples consumed one per
    call, so a test can describe a first sync and a second sync without
    rebuilding the client in between.
    """

    delta_pages: list[tuple[list[dict], str | None]] = field(default_factory=list)
    folder: dict | None = None
    #: The payload `/shares/{token}/driveItem` answers with, when a test
    #: configures a source by sharing link.
    shared: dict | None = None
    downloads: dict[str, bytes] = field(default_factory=dict)
    fail_downloads: set[str] = field(default_factory=set)
    expire_delta_on: set[int] = field(default_factory=set)

    delta_calls: list[str | None] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    _delta_index: int = 0

    # --- the surface the sync service uses -------------------------------

    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        if "/delta" in path_or_url or path_or_url.startswith("delta:"):
            return self._next_delta_payload(path_or_url)

        if "/shares/" in path_or_url:
            if self.shared is None:
                raise GraphRequestError("Graph request failed (HTTP 404).")

            return self.shared

        if self.folder is not None:
            return self.folder

        return drive_item(FOLDER_ID, "Root", folder=True)

    def delta(self, path_or_url: str) -> tuple[list[dict], str | None]:
        self.delta_calls.append(path_or_url)

        incremental = not path_or_url.endswith("/delta")

        if incremental and self._delta_index in self.expire_delta_on:
            # Consume the slot so the full-resync retry gets the next page,
            # exactly as the real client's caller would experience it.
            self.expire_delta_on.discard(self._delta_index)
            raise DeltaTokenExpiredError("Delta token expired.")

        if self._delta_index >= len(self.delta_pages):
            return [], "delta:exhausted"

        items, link = self.delta_pages[self._delta_index]
        self._delta_index += 1

        return items, link

    def paged(self, path_or_url: str, params: dict | None = None):
        payload = self.get(path_or_url, params)
        yield from payload.get("value", [])

    def download(self, url: str) -> bytes:
        self.downloaded.append(url)

        if url in self.fail_downloads:
            raise GraphRequestError("Download failed (HTTP 500).")

        if url not in self.downloads:
            raise GraphRequestError("Download failed (HTTP 404).")

        return self.downloads[url]

    # --- helpers ---------------------------------------------------------

    def _next_delta_payload(self, _path: str) -> dict:
        items, link = self.delta(_path)

        return {"value": items, "@odata.deltaLink": link}

    def register(self, item: dict, content: bytes) -> dict:
        """Make `item` downloadable, returning it for use in a delta page."""

        url = item.get("@microsoft.graph.downloadUrl")
        if url:
            self.downloads[url] = content

        return item


def sources_json(*entries: dict) -> str:
    """Render source configuration the way ONEDRIVE_SOURCES carries it."""

    return json.dumps(list(entries))
