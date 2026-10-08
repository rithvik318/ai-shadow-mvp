"""Every file in the configured source, and what would happen to it.

Run from `backend/`:

    python -m scripts.diagnose_onedrive_corpus            # metadata only
    python -m scripts.diagnose_onedrive_corpus --probe    # + one HEAD per file

This answers the questions a failure count cannot: which files, of what type,
how big, and — with `--probe` — exactly what Graph says when asked for each
one's content. It is **read-only**. It ingests nothing, writes nothing to the
database, and never touches `report_data/`.

`--probe` asks Graph for each item's `/content` redirect and reports the status.
That is one cheap request per file and no downloaded bytes, which is what makes
it safe to run over a corpus that a real sync would take an hour to fetch.

Secrets are never printed: tokens and pre-authorised URLs are reduced to their
host, and query strings — where Graph puts its tokens — are stripped.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

RULE = "=" * 78


def heading(title: str) -> None:
    print()
    print(RULE)
    print(title)
    print(RULE)


def _extension(name: str) -> str:
    return PurePosixPath(name).suffix.lower() or "(none)"


def _table(title: str, counter: Counter, *, limit: int = 40) -> None:
    print()
    print(f"  {title}")

    if not counter:
        print("    (none)")
        return

    width = max(len(str(key)) for key, _ in counter.most_common(limit))

    for key, count in counter.most_common(limit):
        print(f"    {str(key):<{width}}  {count:>6}")

    remaining = len(counter) - limit

    if remaining > 0:
        print(f"    … and {remaining} more distinct values")


def _report_stored_state() -> None:
    """Count what previous runs settled, by status, naming the OCR backlog.

    The question a sync summary cannot answer once the run is over: which files
    are indexed, which are waiting on a format this application does not read,
    and which are scans. The last group is listed by name rather than counted,
    because it is a work queue — not thirty defects, thirty files somebody has
    to put through OCR.
    """

    from sqlalchemy import func, select

    from app.database.database import SessionLocal
    from app.models.document import Document, DocumentStatus

    db = SessionLocal()

    try:
        rows = db.execute(
            select(Document.status, func.count(Document.id))
            .where(Document.source_uri.is_not(None))
            .group_by(Document.status)
        ).all()

        if not rows:
            print("  (no synced documents recorded yet)")
            return

        for status, count in sorted(rows, key=lambda row: -row[1]):
            print(f"    {str(getattr(status, 'value', status)):<14} {count:>6}")

        scans = (
            db.execute(
                select(Document.filename)
                .where(Document.status == DocumentStatus.OCR_REQUIRED)
                .order_by(Document.filename)
            )
            .scalars()
            .all()
        )

        if scans:
            print()
            print(f"  OCR_REQUIRED / NO_TEXT_LAYER — {len(scans)} file(s), deferred:")
            for name in scans:
                print(f"    {name[:70]}")
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="source key; default the first")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="ask Graph for each file's content redirect and record the status",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="probe at most this many files (0 = all)"
    )
    args = parser.parse_args()

    from app.core.exceptions import GraphError
    from app.services.features.sync.onedrive_sync_service import (
        classify_without_downloading,
    )
    from app.services.features.sync.source_config import load_sources
    from app.services.features.sync.source_resolver import resolve_source
    from app.services.graph import drive_service
    from app.services.graph.client import GraphClient

    sources = load_sources()

    if not sources:
        print("No ONEDRIVE_SOURCES configured.")
        return 2

    source = next((s for s in sources if s.key == args.source), sources[0])
    client = GraphClient.from_settings()

    heading(f"SOURCE — {source.key}")
    resolved = resolve_source(client, source)

    if not resolved.resolved:
        print(f"  did not resolve: {resolved.error}")
        return 2

    print(f"  drive …{str(resolved.drive_id)[-12:]}  item {resolved.item_id}")

    heading("ENUMERATION")
    items, _link = drive_service.iter_delta(
        client, drive_id=str(resolved.drive_id), item_id=str(resolved.item_id)
    )

    folders = [item for item in items if item.is_folder]
    deleted = [item for item in items if item.deleted]
    files = [item for item in items if not item.is_folder and not item.deleted]

    print(f"  items returned      {len(items)}")
    print(f"  folders             {len(folders)}")
    print(f"  deleted (tombstone) {len(deleted)}")
    print(f"  files               {len(files)}")

    # --- what the sync would decide, without fetching anything ------------

    would_skip: list[Any] = []
    would_fetch: list[Any] = []

    for item in files:
        if classify_without_downloading(item) is None:
            would_fetch.append(item)
        else:
            would_skip.append(item)

    heading("CLASSIFICATION FROM METADATA (no bytes fetched)")
    print(f"  would be downloaded    {len(would_fetch)}")
    print(f"  rejected before fetch  {len(would_skip)}")

    _table(
        "rejected before fetch, by extension",
        Counter(_extension(item.name) for item in would_skip),
    )
    _table(
        "rejected before fetch, by reason",
        Counter((classify_without_downloading(item) or "")[:70] for item in would_skip),
    )
    _table(
        "would be downloaded, by extension",
        Counter(_extension(item.name) for item in would_fetch),
    )
    _table(
        "would be downloaded, by declared MIME type",
        Counter(item.mime_type or "(none)" for item in would_fetch),
    )

    carries_url = sum(1 for item in would_fetch if item.download_url)
    print()
    print(f"  of those, carrying a download URL from enumeration: {carries_url}")
    print(
        f"  needing a freshly minted URL:                       "
        f"{len(would_fetch) - carries_url}"
    )

    # --- what the database already knows about this corpus ---------------

    heading("ALREADY RECORDED IN THE KNOWLEDGE BASE")
    _report_stored_state()

    if not args.probe:
        print()
        print("  Re-run with --probe to ask Graph what each one actually answers.")
        return 0

    # --- what Graph actually says ----------------------------------------

    heading("PROBE — one content redirect per file, no bytes downloaded")

    probe = would_fetch if not args.limit else would_fetch[: args.limit]
    statuses: Counter = Counter()
    by_extension_failure: Counter = Counter()
    examples: dict[str, str] = {}

    for index, item in enumerate(probe, start=1):
        if index % 50 == 0:
            print(f"    … {index} of {len(probe)}")

        try:
            location = client.redirect_location(
                f"/drives/{item.drive_id}/items/{item.item_id}/content"
            )
            outcome = "redirect issued" if location else "200, no redirect"
        except GraphError as exc:
            status = getattr(exc, "status_code", None)
            code = getattr(exc, "graph_code", None)
            outcome = f"{status or 'error'}{f' {code}' if code else ''}"
            by_extension_failure[_extension(item.name)] += 1
            examples.setdefault(outcome, item.name)

        statuses[outcome] += 1

    _table("Graph's answer per file", statuses)
    _table("failures by extension", by_extension_failure)

    if examples:
        print()
        print("  one example of each failing answer:")
        for outcome, name in sorted(examples.items()):
            print(f"    {outcome:<28} {name[:44]}")

    readable = statuses.get("redirect issued", 0)

    print()
    print(f"  genuinely downloadable: {readable} of {len(probe)} probed")

    return probe_exit_code(readable=readable, probed=len(probe))


def probe_exit_code(*, readable: int, probed: int) -> int:
    """Non-zero when files were offered and Graph would not hand over one.

    A corpus with nothing to probe is not a failure — an empty folder and a
    folder whose every file is refused are different facts, and collapsing
    them is how "the sync reported success" started meaning nothing.
    """

    if probed == 0:
        return 0

    return 0 if readable else 1


if __name__ == "__main__":
    sys.exit(main())
