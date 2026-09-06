"""Find the drive and folder ids needed to configure `ONEDRIVE_SOURCES`.

Configuration wants a `drive_id` and either a `path` or an `item_id`. Those
are not things anyone knows by heart, and hunting them down in Graph Explorer
by hand is where a five-folder setup goes wrong. This asks Graph for them, and
prints a `ONEDRIVE_SOURCES` line ready to paste into `.env`.

Read-only. It enumerates and prints; it never writes to Graph, never touches
the database, and never prints a credential.

    cd backend
    python -m scripts.discover_onedrive_sources --user someone@sunradia.com
    python -m scripts.discover_onedrive_sources --site <host>:/sites/<name>
    python -m scripts.discover_onedrive_sources --drive <drive-id> --list "Capabilities"

Once `ONEDRIVE_SOURCES` names the folders by path, `--resolve` turns every one
of them into a `drive_id` + `item_id` pair and prints the configuration line
that pins them:

    python -m scripts.discover_onedrive_sources --resolve

Nothing here derives an id from a URL. Every id printed came from Graph
answering a question about a path, which is the difference between a folder
that is definitely the right one and a folder that looks like it.
"""

import argparse
import json
import sys

from app.config.settings import settings
from app.core.exceptions import SyncError
from app.services.features.sync import source_resolver
from app.services.features.sync.source_config import load_sources
from app.services.graph import drive_service
from app.services.graph.client import GraphClient


def _print_drives(client: GraphClient, path: str, label: str) -> list[dict]:
    payload = client.get(path)
    drives = payload.get("value", [payload]) if "value" in payload else [payload]

    print(f"\n{label}")
    print("=" * len(label))

    for drive in drives:
        print(f"  name      : {drive.get('name')}")
        print(f"  driveType : {drive.get('driveType')}")
        print(f"  drive_id  : {drive.get('id')}")
        owner = (drive.get("owner") or {}).get("user", {}).get("displayName")
        if owner:
            print(f"  owner     : {owner}")
        print()

    return drives


def _walk(client: GraphClient, drive_id: str, item_id: str, prefix: str = "") -> int:
    """Print the folder tree under `item_id`, returning the file count."""

    files = 0

    for payload in client.paged(f"/drives/{drive_id}/items/{item_id}/children"):
        name = payload.get("name", "?")

        if "folder" in payload:
            child_count = (payload.get("folder") or {}).get("childCount", "?")
            print(f"  {prefix}{name}/   ({child_count} items)")
            print(f"  {prefix}    item_id: {payload.get('id')}")
            files += _walk(client, drive_id, str(payload.get("id")), prefix + "    ")
        else:
            files += 1

    return files


def _report(source) -> None:
    """One block per source, saying what happened and what would fix it."""

    state = "enabled" if source.enabled else "disabled"
    print(f"  {source.key}  ({state})")
    print(f"    label     : {source.label}")

    if source.path:
        print(f"    path      : {source.path}")
    if source.resolved_url:
        # The destination of a shortened link is the single fact that decides
        # whether any of this is reachable, so it is always shown.
        print(f"    points to : {source.resolved_url}")
    if source.kind:
        print(f"    kind      : {source.kind.value}")

    if source.resolved:
        print("    verdict   : RESOLVED")
        print(f"    drive_id  : {source.drive_id}")
        print(f"    item_id   : {source.item_id}")
    else:
        print(f"    verdict   : {source.verdict.value.upper()}")
        print(f"    reason    : {source.error}")
        if source.remedy:
            print(f"    remedy    : {source.remedy}")

    print()


def _resolve(client: GraphClient) -> int:
    """Resolve every configured source and print the pinned configuration.

    Returns non-zero if any source could not be resolved, so this can be used
    as a deployment check rather than only as a convenience.
    """

    sources = load_sources()

    if not sources:
        print(
            "ONEDRIVE_SOURCES is empty. Add the folders to synchronise first — "
            "by path, by item id, or by sharing link — then run this to pin "
            "them to ids.",
            file=sys.stderr,
        )
        return 2

    resolved = source_resolver.resolve_sources(client, sources)

    print("\nSources")
    print("=======")

    for source in resolved:
        _report(source)

    configuration = source_resolver.to_configuration(resolved)

    if configuration:
        print("Configuration line for .env:")
        print("ONEDRIVE_SOURCES=" + json.dumps(configuration))

    failed = [source for source in resolved if not source.resolved]

    if not failed:
        return 0

    outside = [
        source
        for source in failed
        if source.verdict is source_resolver.Verdict.OUTSIDE_TENANT
    ]
    denied = [
        source
        for source in failed
        if source.verdict is source_resolver.Verdict.ACCESS_DENIED
    ]

    print(f"\n{len(failed)} source(s) could not be resolved.", file=sys.stderr)

    if outside:
        print(
            "\n"
            + str(len(outside))
            + " of them are outside this Entra tenant. No permission granted to "
            "this application can reach them, because an application token has "
            "authority only inside the tenant that issued it. The content has "
            "to be brought into the tenant:\n  "
            + "\n  ".join(source.key for source in outside),
            file=sys.stderr,
        )

    if denied:
        print(
            "\n"
            + str(len(denied))
            + " of them are inside the tenant but refused. That is a consent "
            "decision for an administrator, not a code change:\n  "
            + "\n  ".join(source.key for source in denied),
            file=sys.stderr,
        )

    return 1


def _diagnose(client: GraphClient, drive_id: str | None) -> int:
    """Everything needed to place the blame, in one run.

    Written to end a diagnostic loop rather than continue one: it resolves
    every source, and — when a drive is known — also lists that drive's root
    and searches it for each configured folder name. A path that returns
    `itemNotFound` is either wrong or one level deeper than somebody wrote
    down, and those two look identical until something searches.
    """

    sources = load_sources()

    if not sources:
        print("ONEDRIVE_SOURCES is empty; nothing to diagnose.", file=sys.stderr)
        return 2

    exit_code = _resolve(client)

    drive_id = drive_id or settings.ONEDRIVE_DRIVE_ID

    if not drive_id:
        return exit_code

    print("\nDrive contents")
    print("==============")
    print(f"  drive_id: {drive_id}")

    try:
        print("\n  Root children:")
        for payload in client.paged(f"/drives/{drive_id}/root/children"):
            kind = "folder" if "folder" in payload else "file"
            shortcut = " -> shortcut" if payload.get("remoteItem") else ""
            print(f"    [{kind}] {payload.get('name')}{shortcut}")
    except SyncError as exc:
        print(f"    Could not list the drive root: {exc}", file=sys.stderr)
        return exit_code

    print("\n  Search for each configured folder name:")

    for source in sources:
        # The last path segment is the folder's own name; searching for the
        # whole path finds nothing, because search matches names.
        leaf = (source.path or source.label).rstrip("/").split("/")[-1]

        try:
            found = drive_service.search_folder(client, drive_id=drive_id, query=leaf)
        except SyncError as exc:
            print(f"    {leaf!r}: search failed ({exc})", file=sys.stderr)
            continue

        if not found:
            print(f"    {leaf!r}: no match anywhere in this drive")
            continue

        for item in found:
            where = item.parent_path or "?"
            print(f"    {leaf!r}: {item.name}  in {where}  item_id={item.item_id}")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="User principal name, for their OneDrive")
    group.add_argument("--site", help="SharePoint site, as hostname:/sites/Name")
    group.add_argument("--drive", help="A known drive id, to enumerate folders in")
    group.add_argument(
        "--resolve",
        action="store_true",
        help="Resolve every source in ONEDRIVE_SOURCES to drive_id + item_id",
    )
    group.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Resolve every source and, for a known drive, list its root and "
            "search it for each configured folder name. One run, no loop."
        ),
    )
    parser.add_argument(
        "--list",
        dest="folder",
        help="With --drive: a folder path to walk and count files in",
    )
    arguments = parser.parse_args()

    try:
        # Credentials come from settings, which reads .env. Nothing is passed
        # on the command line, so none of this can end up in shell history.
        client = GraphClient.from_settings()
    except SyncError as exc:
        print(f"Not configured: {exc}", file=sys.stderr)
        return 2

    try:
        if arguments.resolve:
            return _resolve(client)

        if arguments.diagnose:
            return _diagnose(client, arguments.drive)

        if arguments.user:
            _print_drives(client, f"/users/{arguments.user}/drive", "OneDrive")
        elif arguments.site:
            site = client.get(f"/sites/{arguments.site}")
            print(f"\nSite: {site.get('displayName')}  ({site.get('id')})")
            _print_drives(
                client, f"/sites/{site.get('id')}/drives", "Document libraries"
            )
        else:
            drive_id = arguments.drive

            if arguments.folder:
                cleaned = arguments.folder.strip("/")
                folder = client.get(f"/drives/{drive_id}/root:/{cleaned}:")
                print(f"\n{arguments.folder}")
                print(f"  item_id: {folder.get('id')}")
                print("\nContents:")
                total = _walk(client, drive_id, str(folder.get("id")))
                print(f"\n  {total} file(s) beneath this folder.")

                print("\nConfiguration line for .env:")
                print(
                    "ONEDRIVE_SOURCES="
                    + json.dumps(
                        [
                            {
                                "key": cleaned.lower()
                                .replace("/", "-")
                                .replace(" ", "-"),
                                "label": cleaned,
                                "drive_id": drive_id,
                                "item_id": folder.get("id"),
                            }
                        ]
                    )
                )
            else:
                print("\nTop-level folders:")
                for payload in client.paged(f"/drives/{drive_id}/root/children"):
                    kind = "folder" if "folder" in payload else "file"
                    print(f"  [{kind}] {payload.get('name')}   id={payload.get('id')}")
    except SyncError as exc:
        # Graph errors already have their query strings stripped.
        print(f"\nGraph error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
