"""Reading a OneDrive browser address, without inventing anything from it.

An administrator hands over the URL from their address bar while looking at a
folder. That URL is not a sharing link and `/shares/{token}` is the wrong
question to ask about it — but it does state, in plain text, two facts worth
having: which personal site the folder is on, and where the folder sits inside
it.

**What this extracts, and what it refuses to.** It reads the *site name* and
the *folder path*, because both are human-readable and both are then handed to
Graph to resolve. It does **not** derive a drive id, an item id, or a user
principal name.

The UPN refusal is the one that matters. A personal site is named by replacing
every `@` and `.` in the address with `_`, so `sudha_gummuluru_sunradia_com`
could be `sudha.gummuluru@sunradia.com` or `sudha@gummuluru.sunradia.com` —
the transformation is not reversible, and a guess here would silently address
a different person's drive or none at all. So the site name is reported and the
operator supplies the address, which they know.

**The `Documents` prefix is the trap.** A OneDrive for Business web URL reads

    /personal/<site>/Documents/Knowledge Base

but Graph's drive *root* for that site **is** `Documents`. The path to
configure is therefore `Knowledge Base`, not `Documents/Knowledge Base` —
configure the latter and every call returns a 404 for a folder that is plainly
there. Stripping that prefix correctly is most of this module's reason to
exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

#: The default document library of a personal site. It *is* the drive root, so
#: it never appears in a Graph path.
_LIBRARY_ROOT = "Documents"

#: Hosts whose URLs this module can read. A personal site lives on the `-my`
#: host; a team site on the bare one. Anything else — a consumer OneDrive link
#: especially — is not a tenant address and is refused rather than parsed into
#: something that looks usable.
_PERSONAL_SUFFIX = "-my.sharepoint.com"
_TEAM_SUFFIX = ".sharepoint.com"


@dataclass(frozen=True)
class DriveLocation:
    """What a web URL says about where a folder is."""

    host: str
    #: The personal site's name, as it appears in the URL — for example
    #: `sudha_gummuluru_sunradia_com`. Not a user principal name, and
    #: deliberately not converted into one.
    site: str
    #: The folder path relative to the **drive root**, with the `Documents`
    #: library prefix removed. Empty when the URL points at the library itself.
    path: str
    #: True for a `*-my.sharepoint.com` address: somebody's OneDrive for
    #: Business, which is addressed through `/users/{upn}/drive`.
    is_personal: bool

    @property
    def needs_user_principal_name(self) -> bool:
        """Whether an operator still has to supply an address.

        Always true for a personal site, because the site name cannot be turned
        back into one. A team site is addressed by its drive instead.
        """

        return self.is_personal


class UnreadableDriveUrl(ValueError):
    """The URL is not a tenant OneDrive or SharePoint address this can read."""


def _folder_path(url: str) -> str:
    """The folder the URL is displaying, decoded.

    Web URLs put it in the `id` query parameter — `?id=%2Fpersonal%2F…` — and
    a direct link puts it in the path. Both are read; neither is guessed at.
    """

    parts = urlsplit(url)
    query = parse_qs(parts.query)

    for name in ("id", "RootFolder"):
        values = query.get(name)

        if values and values[0].strip():
            return unquote(values[0])

    return unquote(parts.path)


def _strip_library(segments: list[str]) -> list[str]:
    """Drop `/personal/<site>` and the `Documents` library from a path.

    Both are above the drive root as Graph sees it, so both have to go. Leaving
    either in place produces a path Graph cannot find.
    """

    if segments[:1] == ["personal"] and len(segments) >= 2:
        segments = segments[2:]

    if segments[:1] == [_LIBRARY_ROOT]:
        segments = segments[1:]

    # A team site names its library instead — `/sites/<name>/Shared Documents`.
    if segments[:1] == ["sites"] and len(segments) >= 2:
        segments = segments[2:]

    if segments[:1] == ["Shared Documents"]:
        segments = segments[1:]

    return segments


def parse(url: str) -> DriveLocation:
    """Read a OneDrive or SharePoint web address.

    Raises `UnreadableDriveUrl` for anything that is not a tenant address —
    including a `1drv.ms` or `onedrive.live.com` link, which points at consumer
    OneDrive and cannot be reached by an application token at all.
    """

    cleaned = (url or "").strip()

    if not cleaned:
        raise UnreadableDriveUrl("No URL was given.")

    host = (urlsplit(cleaned).hostname or "").lower()

    if not host.endswith(_TEAM_SUFFIX):
        raise UnreadableDriveUrl(
            f"{host or 'that address'} is not a tenant SharePoint or OneDrive "
            "for Business host. Only a *.sharepoint.com address can be read "
            "here; a 1drv.ms or onedrive.live.com link points at consumer "
            "OneDrive, which no application token can reach."
        )

    is_personal = host.endswith(_PERSONAL_SUFFIX)

    raw = _folder_path(cleaned)
    segments = [segment for segment in raw.split("/") if segment]

    site = ""

    if segments[:1] == ["personal"] and len(segments) >= 2:
        site = segments[1]

    trimmed = _strip_library(segments)

    # `_layouts/15/onedrive.aspx` is the viewer, not the folder. It appears in
    # the URL path while the folder itself is in the `id` parameter, so a URL
    # whose path was read instead would produce a path into the site's
    # machinery.
    if trimmed[:1] == ["_layouts"]:
        trimmed = []

    return DriveLocation(
        host=host,
        site=site,
        path="/".join(trimmed),
        is_personal=is_personal,
    )


def describe(location: DriveLocation, *, key: str = "knowledge-base") -> str:
    """The configuration this location implies, written for an operator.

    Deliberately a *template* rather than a finished value for a personal site:
    the address cannot be derived, so the line says where to put it instead of
    filling in a guess.
    """

    folder = location.path or "(the library root)"

    if location.is_personal:
        return (
            f"Personal site {location.site!r} on {location.host}.\n"
            f"Folder, relative to the drive root: {folder}\n\n"
            "Add to ONEDRIVE_SOURCES, replacing <user> with that person's "
            "address (the site name cannot be converted back into one):\n"
            f'  {{"key": "{key}", "label": "Knowledge Base", '
            f'"user": "<user>@example.com", "path": "{location.path}"}}'
        )

    return (
        f"Team site on {location.host}.\n"
        f"Folder, relative to the drive root: {folder}\n\n"
        "Add to ONEDRIVE_SOURCES, with the library's drive id:\n"
        f'  {{"key": "{key}", "label": "Knowledge Base", '
        f'"drive_id": "<drive-id>", "path": "{location.path}"}}'
    )
