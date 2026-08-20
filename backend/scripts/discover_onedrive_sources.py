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
"""

import argparse
import json
import sys

from app.core.exceptions import SyncError
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="User principal name, for their OneDrive")
    group.add_argument("--site", help="SharePoint site, as hostname:/sites/Name")
    group.add_argument("--drive", help="A known drive id, to enumerate folders in")
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
