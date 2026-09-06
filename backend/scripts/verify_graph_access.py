"""Prove — or disprove — that this application can actually reach OneDrive.

Read-only. It enumerates and counts; it writes nothing to Graph, touches no
database, and ingests nothing. Run it before trusting any sync result, because
every Graph behaviour in this repository is otherwise verified only against a
mock transport.

    cd backend
    python -m scripts.verify_graph_access

**Nothing secret is ever printed.** Not the client secret, not the access
token, not a delta link, not a pre-authorised download URL. Drive and item ids
are abbreviated, because a full drive id plus a tenant is enough for somebody
reading over your shoulder to address your storage. Pass `--full-ids` when you
actually need one to paste into `ONEDRIVE_SOURCES`.

The output is designed to be pasteable into a chat or a ticket as-is.

What it distinguishes, because the remedies differ completely:

- **not configured** — no credentials in `.env`. Nothing is wrong; nothing is
  set up.
- **credentials rejected** — the token will not mint. A tenant/client/secret
  problem, and retrying will not help.
- **consent missing** — the token mints but Graph answers 403. The application
  registration lacks `Files.Read.All` (or admin consent for it), which is a
  different fix from bad credentials and a different page in the portal.
- **resource not found** — credentials and consent are fine, but the drive or
  folder named in configuration does not exist or is not shared with the app.
"""

import argparse
from collections import Counter

from app.config.settings import settings
from app.core.constants import SUPPORTED_EXTENSIONS
from app.core.exceptions import (
    GraphAuthError,
    GraphError,
    SyncNotConfiguredError,
)
from app.services.features.sync.source_config import load_sources
from app.services.graph.client import GraphClient

# Enough to recognise an id you already know, far too little to use.
_ID_PREFIX = 8


def _short(identifier: str | None, *, full: bool) -> str:
    if not identifier:
        return "(none)"

    if full or len(identifier) <= _ID_PREFIX:
        return identifier

    return f"{identifier[:_ID_PREFIX]}…({len(identifier)} chars)"


def _extension(name: str) -> str:
    _, _, tail = name.rpartition(".")

    return f".{tail.lower()}" if tail and tail != name else "(none)"


def _describe_drive(drive: dict, *, full: bool) -> str:
    owner = (drive.get("owner") or {}).get("user", {}).get("displayName")
    quota = drive.get("quota") or {}

    parts = [
        f"name={drive.get('name')!r}",
        f"type={drive.get('driveType')}",
        f"id={_short(drive.get('id'), full=full)}",
    ]

    if owner:
        parts.append(f"owner={owner!r}")

    if quota.get("total"):
        used = round((quota.get("used") or 0) / 1e9, 1)
        total = round(quota["total"] / 1e9, 1)
        parts.append(f"used={used}/{total} GB")

    return "  " + "\n  ".join(parts)


def _try(label: str, call, *, full: bool) -> dict | None:
    """Run one Graph read and report the outcome in the caller's language."""

    print(f"\n── {label}")

    try:
        payload = call()
    except SyncNotConfiguredError as exc:
        print(f"   NOT CONFIGURED: {exc}")
        return None
    except GraphAuthError as exc:
        # Graph answers 403 both for "no consent" and for "consent, but not to
        # this resource". The message carries whichever detail Graph gave.
        print(f"   DENIED: {exc}")
        print(
            "   → the token minted, so credentials are right. This is a "
            "permission or sharing problem, not a secret problem."
        )
        return None
    except GraphError as exc:
        print(f"   FAILED: {exc}")
        return None

    print("   OK")

    return payload


def _walk_counts(
    client: GraphClient, drive_id: str, item_id: str, *, depth: int = 0
) -> tuple[Counter, int]:
    """Count files by extension beneath a folder, recursively.

    Returns `(extension counts, folder count)`. Bounded only by the folder
    tree: this is a survey, and an undercount would defeat the point of
    running it.
    """

    extensions: Counter = Counter()
    folders = 0

    for entry in client.paged(f"/drives/{drive_id}/items/{item_id}/children"):
        name = str(entry.get("name") or "")

        if "folder" in entry:
            folders += 1
            child_extensions, child_folders = _walk_counts(
                client, drive_id, str(entry.get("id")), depth=depth + 1
            )
            extensions.update(child_extensions)
            folders += child_folders
        else:
            extensions[_extension(name)] += 1

    return extensions, folders


def _report_coverage(extensions: Counter) -> tuple[int, int]:
    supported = sum(
        count for suffix, count in extensions.items() if suffix in SUPPORTED_EXTENSIONS
    )
    unsupported = sum(extensions.values()) - supported

    for suffix, count in extensions.most_common():
        mark = "ingestible" if suffix in SUPPORTED_EXTENSIONS else "NOT ingestible"
        print(f"     {suffix:<12} {count:>5}   {mark}")

    return supported, unsupported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full-ids",
        action="store_true",
        help="Print complete drive and item ids, for pasting into .env.",
    )
    arguments = parser.parse_args()
    full = arguments.full_ids

    print("Microsoft Graph access check")
    print("=" * 60)
    print(f"  base url        : {settings.ONEDRIVE_GRAPH_BASE_URL}")
    print(f"  authority       : {settings.ONEDRIVE_AUTHORITY}")
    print(f"  tenant id       : {'set' if settings.ONEDRIVE_TENANT_ID else 'MISSING'}")
    print(f"  client id       : {'set' if settings.ONEDRIVE_CLIENT_ID else 'MISSING'}")
    print(
        f"  client secret   : "
        f"{'set (never printed)' if settings.ONEDRIVE_CLIENT_SECRET else 'MISSING'}"
    )
    print(f"  configured drive: {_short(settings.ONEDRIVE_DRIVE_ID, full=full)}")

    try:
        client = GraphClient.from_settings()
    except SyncNotConfiguredError as exc:
        print(f"\nNOT CONFIGURED: {exc}")
        return 2

    # 1. Can a token be minted at all? Everything else is meaningless if not.
    print("\n── minting an application token")
    try:
        client.access_token()
        print("   OK — client credentials accepted")
    except GraphError as exc:
        print(f"   FAILED: {exc}")
        print("   → check ONEDRIVE_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET.")
        return 1

    # 2. The bare collection. Expected to fail under application permissions —
    #    Graph wants a context (/users/{id}/drives, /sites/{id}/drives). It is
    #    checked anyway because "GET /drives is 400" is itself a useful answer,
    #    and rules out a whole class of misunderstanding.
    listing = _try(
        "GET /drives (no context — often invalid app-only)",
        lambda: client.get("/drives"),
        full=full,
    )

    if listing and listing.get("value"):
        print(f"   {len(listing['value'])} drive(s) visible:")
        for drive in listing["value"]:
            print(_describe_drive(drive, full=full))

    # 3. The drive configuration actually names. This is the one that matters.
    drive_id = settings.ONEDRIVE_DRIVE_ID

    if not drive_id:
        print(
            "\nONEDRIVE_DRIVE_ID is not set, so there is no drive to test "
            "directly. Set it, or use scripts/discover_onedrive_sources.py "
            "with --user or --site to find one."
        )
        return 1

    drive = _try(
        f"GET /drives/{_short(drive_id, full=full)}",
        lambda: client.get(f"/drives/{drive_id}"),
        full=full,
    )

    if drive is None:
        print("\nThe configured drive is not reachable. Nothing below can run.")
        return 1

    print(_describe_drive(drive, full=full))

    root = _try(
        "GET /drives/{id}/root/children",
        lambda: client.get(f"/drives/{drive_id}/root/children", {"$top": 200}),
        full=full,
    )

    if root is not None:
        entries = root.get("value", [])
        print(f"   {len(entries)} item(s) at the drive root:")
        for entry in entries:
            kind = "folder" if "folder" in entry else "file "
            print(f"     [{kind}] {entry.get('name')}")

    # 4. Each configured source folder, with a real supported/unsupported split.
    try:
        sources = load_sources()
    except Exception as exc:  # noqa: BLE001 - a malformed config is a finding
        print(f"\nONEDRIVE_SOURCES could not be read: {exc}")
        return 1

    if not sources:
        print(
            "\nONEDRIVE_SOURCES is empty, so no folders are configured to "
            "synchronise. The drive is reachable; there is simply nothing "
            "pointed at yet."
        )
        return 0

    print(f"\n── {len(sources)} configured source(s)")
    grand_supported = grand_unsupported = 0

    for source in sources:
        source_drive = source.drive_id or drive_id
        print(f"\n   {source.key}  ({source.label})")

        try:
            if source.item_id:
                folder = client.get(f"/drives/{source_drive}/items/{source.item_id}")
            else:
                cleaned = str(source.path).strip("/")
                folder = client.get(f"/drives/{source_drive}/root:/{cleaned}:")
        except GraphError as exc:
            print(f"     NOT REACHABLE: {exc}")
            continue

        item_id = str(folder.get("id"))
        print(f"     resolved item_id: {_short(item_id, full=full)}")

        try:
            extensions, folders = _walk_counts(client, source_drive, item_id)
        except GraphError as exc:
            print(f"     enumeration failed: {exc}")
            continue

        total = sum(extensions.values())
        print(f"     {total} file(s) in {folders} subfolder(s):")
        supported, unsupported = _report_coverage(extensions)
        print(f"     → {supported} ingestible, {unsupported} not ingestible")

        grand_supported += supported
        grand_unsupported += unsupported

    print("\n" + "=" * 60)
    print(
        f"TOTAL across configured sources: {grand_supported} ingestible, "
        f"{grand_unsupported} not ingestible "
        f"({grand_supported + grand_unsupported} files seen)"
    )
    print(
        "\n'Not ingestible' means no parser exists for that extension today — "
        "the file is reported and skipped, never silently dropped. Supported: "
        + ", ".join(sorted(SUPPORTED_EXTENSIONS))
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
