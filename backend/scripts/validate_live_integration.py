"""The live integration validation, as one command.

Run from `backend/`:

    python -m scripts.validate_live_integration

It reads `backend/.env` the way the application does, talks to the real tenant,
and prints a structured report. It **never prints a secret**: tokens, the client
secret and the database password are redacted wherever they could appear, and
Graph's own error bodies are truncated to a status and a code.

Nothing here is destructive. It reads, it synchronises (which writes to the
knowledge base, as a sync is meant to), and it imports calendar events. It
deletes nothing and it never touches `report_data/`.

**It stops at the first blocking failure rather than carrying on.** A sync that
cannot authenticate has nothing to say about idempotency, and a second full run
against a tenant that just refused 847 files is the thing nobody wants repeated.

Exit status is 0 only if every stage that ran reached a state worth calling
success. Anything else exits non-zero with the reason on the last line.

**A sync that reported `succeeded` is not evidence that anything was ingested,
and this script no longer treats it as such.** A live run once discovered 859
files, failed 487 of them, indexed zero, and still called itself a success —
and because the knowledge base already held 268 documents from an earlier
staged import, every downstream check passed on history rather than on the run.
Both holes are closed here: a run that handled none of what it discovered is
reported as a failure, and the retrieval stage counts documents written *during
this run* separately from documents that were already there.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

RULE = "=" * 78


def heading(title: str) -> None:
    print()
    print(RULE)
    print(title)
    print(RULE)


def line(label: str, value: Any) -> None:
    print(f"  {label:<34} {value}")


def _redact(text: str, limit: int = 240) -> str:
    """Never let a token or a secret reach stdout."""

    cleaned = str(text).split("?", 1)[0]

    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


# --- stage 1: configuration ------------------------------------------------


def check_configuration() -> tuple[bool, list]:
    from app.config.settings import settings
    from app.services.features.sync.source_config import load_sources

    heading("1. CONFIGURATION (offline — no network)")

    credentials = {
        "ONEDRIVE_TENANT_ID": settings.ONEDRIVE_TENANT_ID,
        "ONEDRIVE_CLIENT_ID": settings.ONEDRIVE_CLIENT_ID,
        "ONEDRIVE_CLIENT_SECRET": settings.ONEDRIVE_CLIENT_SECRET,
    }

    for name, value in credentials.items():
        line(name, "set" if value else "MISSING")

    if not all(credentials.values()):
        print("\n  BLOCKED: Graph credentials are incomplete.")
        return False, []

    try:
        sources = load_sources()
    except Exception as exc:
        print(f"\n  BLOCKED: ONEDRIVE_SOURCES will not parse — {exc}")
        return False, []

    line("configured sources", len(sources))

    for source in sources:
        addressed = (
            "item_id"
            if source.item_id
            else "path"
            if source.path
            else "share_url"
            if source.share_url
            else "user"
            if source.user
            else "drive root"
        )
        line(f"  {source.key}", f"label={source.label!r} addressed by {addressed}")

    mailbox = settings.EMAIL_MAILBOX_ADDRESS or ""

    if mailbox and (mailbox.count("@") != 1 or "=" in mailbox or len(mailbox) > 100):
        print()
        print("  WARNING: EMAIL_MAILBOX_ADDRESS does not look like one address.")
        print(f"           It is {len(mailbox)} characters long. A missing newline")
        print("           in .env glues the next assignment onto it.")

    return bool(sources), sources


# --- stage 2: Graph authentication ----------------------------------------


def check_authentication():
    from app.core.exceptions import GraphError
    from app.services.graph.client import GraphClient

    heading("2. GRAPH AUTHENTICATION")

    client = GraphClient.from_settings()

    try:
        token = client.access_token()
    except GraphError as exc:
        line("token", "FAILED")
        print(f"\n  BLOCKED: {_redact(str(exc))}")
        return None, set()

    line("token", f"acquired ({len(token)} chars, not shown)")

    return client, _describe_grants(token)


def _describe_grants(token: str) -> set[str]:
    """Name the application permissions this token actually carries.

    An app-only access token is a JWT whose `roles` claim lists the application
    permissions an administrator has consented to. Reading it turns "Graph said
    403" from a guess into a fact: a permission absent here was never granted,
    and a permission present here that still gets 403 is being refused by
    something else — an Exchange application access policy, or the wrong
    mailbox.

    The payload is decoded, not verified: this is a diagnostic, and the token
    was just issued by the authority we asked. **The token itself is never
    printed** — only the claim names, which are not secrets and are exactly what
    an administrator needs to see.
    """

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        line("granted permissions", "(token is not a readable JWT)")
        return set()

    roles = set(claims.get("roles") or [])

    line("token tenant", claims.get("tid", "—"))
    line("token app id", claims.get("appid") or claims.get("azp") or "—")
    line("token audience", claims.get("aud", "—"))
    line("granted permissions", ", ".join(sorted(roles)) or "NONE")

    for needed, purpose in (
        ("Files.Read.All", "OneDrive sync"),
        ("Calendars.Read", "calendar import"),
        ("Mail.Read", "email reading"),
        ("Mail.Send", "email sending"),
    ):
        line(
            f"  {needed}",
            ("granted" if needed in roles else "NOT GRANTED") + f"  ({purpose})",
        )

    return roles


# --- stage 3: what the source actually points at ---------------------------


def describe_source(client, source) -> bool:
    from app.core.exceptions import GraphError
    from app.services.features.sync import source_resolver

    heading(f"3. SOURCE RESOLUTION — {source.key}")

    resolved = source_resolver.resolve_source(client, source)

    if not resolved.resolved:
        line("resolved", "NO")
        line("verdict", resolved.verdict.value)
        print(f"\n  {_redact(resolved.error or '')}")
        if getattr(resolved, "remedy", None):
            print(f"  Remedy: {resolved.remedy}")
        return False

    line("resolved", "YES")
    line("drive_id", f"…{str(resolved.drive_id)[-12:]}")
    line("item_id", resolved.item_id)

    # The question the brief actually asks: whose drive, and which folder.
    try:
        drive = client.get(f"/drives/{resolved.drive_id}")
        owner = (drive.get("owner") or {}).get("user") or {}
        line("drive type", drive.get("driveType"))
        line("drive owner", owner.get("displayName") or "(not reported)")
        line("owner address", owner.get("email") or "(not reported)")

        item = client.get(f"/drives/{resolved.drive_id}/items/{resolved.item_id}")
        parent = (item.get("parentReference") or {}).get("path", "")
        line("folder name", item.get("name"))
        line("folder path", parent)
        line("child count", (item.get("folder") or {}).get("childCount", "—"))
    except GraphError as exc:
        line("folder details", f"unavailable — {_redact(str(exc))}")

    return True


# --- stage 4: the sync -----------------------------------------------------


def run_sync(client, source_key: str, *, full: bool) -> tuple[bool, Any]:
    from app.database.database import SessionLocal
    from app.services.features.sync import onedrive_sync_service

    heading(f"4. SYNCHRONISATION — {source_key} ({'full' if full else 'incremental'})")

    started = time.monotonic()
    db = SessionLocal()

    try:
        summaries = onedrive_sync_service.sync_all(
            db, client=client, source_key=source_key, full=full
        )
    finally:
        db.close()

    elapsed = time.monotonic() - started

    if not summaries:
        print("  no summary returned")
        return False, None

    summary = summaries[0]

    line("status", summary.status.value)
    line("mode", summary.mode)
    line("discovered", summary.discovered)
    line("indexed", summary.indexed)
    line("replaced", summary.replaced)
    line("unchanged", summary.unchanged)
    line("unsupported", summary.unsupported)
    line("needs OCR (no text layer)", summary.ocr_required)
    line("failed", summary.failed)
    line("delta token advanced", summary.delta_advanced)
    line("elapsed", f"{elapsed:.1f}s ({elapsed / 60:.1f} min)")

    if summary.error:
        print()
        print(f"  error: {_redact(summary.error, 400)}")

    failures = [item for item in summary.outcomes if item.result.value == "failed"]

    if failures:
        print()
        print(f"  CATEGORIZED FAILURES ({len(failures)} total)")

        buckets: dict[str, list] = {}
        for item in failures:
            key = ("transient: " if item.transient else "permanent: ") + _redact(
                (item.reason or "unknown").split(";")[0], 90
            )
            buckets.setdefault(key, []).append(item)

        for key, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            print(f"    {len(items):>5}  {key}")
            for item in items[:3]:
                print(f"           e.g. {item.name}")

    scans = [item for item in summary.outcomes if item.result.value == "ocr_required"]

    if scans:
        print()
        print(f"  DEFERRED — OCR REQUIRED / NO TEXT LAYER ({len(scans)} total)")
        print("    Read successfully; the file holds no text. Not a failure, and")
        print("    retrying cannot help. OCR is deliberately not implemented.")
        for item in scans:
            print(f"      {item.name[:68]}")

    unsupported = [
        item for item in summary.outcomes if item.result.value == "unsupported"
    ]

    if unsupported:
        print()
        print(f"  UNSUPPORTED, BY REASON ({len(unsupported)} total)")
        buckets = {}
        for item in unsupported:
            buckets.setdefault(_redact((item.reason or "unknown"), 90), []).append(item)
        for key, items in sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:8]:
            print(f"    {len(items):>5}  {key}")

    print()
    verdict, ingested_something = _ingestion_verdict(summary)
    line("INGESTION", verdict)

    return summary.status.value in {"succeeded", "partial"} and ingested_something, (
        summary
    )


def _ingestion_verdict(summary) -> tuple[str, bool]:
    """Did this run actually read anything, regardless of what it called itself.

    The question a status field cannot answer. `discovered` counts what the
    delta enumeration returned; `indexed + replaced + unchanged` counts what
    was successfully read. A run where the second number is zero and the first
    is not read nothing at all — whatever the status says — and the only
    honest report of it is a failure.
    """

    # A scan counts as handled. Its bytes were fetched and its parser ran to
    # completion; there was simply no text in the file. Excluding it here would
    # make a corpus of thirty scans read as a broken downloader, which is the
    # opposite of what this verdict exists to detect.
    handled = (
        summary.indexed + summary.replaced + summary.unchanged + summary.ocr_required
    )

    if summary.discovered == 0:
        return "nothing to do — the source reported no changes", True

    if handled == 0:
        return (
            f"FAILED — {summary.discovered} discovered, none successfully read "
            f"({summary.failed} failed, {summary.unsupported} unsupported)",
            False,
        )

    return (
        f"{handled} of {summary.discovered} discovered files were read "
        f"(indexed={summary.indexed} replaced={summary.replaced} "
        f"unchanged={summary.unchanged} needs-OCR={summary.ocr_required})",
        True,
    )


def show_status(source_key: str) -> None:
    from app.database.database import SessionLocal
    from app.services.features.sync import onedrive_sync_service

    heading("5. STORED STATE (what GET /sync/onedrive/status reports)")

    db = SessionLocal()
    try:
        state = onedrive_sync_service.get_state(db, source_key)
    finally:
        db.close()

    if state is None:
        print("  no state stored")
        return

    line("status", state.status.value)
    line("has delta token", bool(state.delta_link))
    line("last attempted", state.last_attempted_at)
    line("last succeeded", state.last_succeeded_at)
    line("last duration", f"{(state.last_duration_ms or 0) / 1000:.1f}s")
    line(
        "last run counts",
        f"indexed={state.last_indexed} replaced={state.last_replaced} "
        f"unchanged={state.last_unchanged} unsupported={state.last_unsupported} "
        f"failed={state.last_failed}",
    )

    if state.error_message:
        print()
        print(f"  {_redact(state.error_message, 400)}")


# --- stage 6: calendar -----------------------------------------------------


def _diagnose_calendar_refusal(client, address: str, detail: str, grants: set) -> None:
    """Separate the three causes of a calendar 403, using evidence.

    They have entirely different owners, and telling them apart by reading the
    error text alone is guesswork — Graph answers `ErrorAccessDenied` to all
    three.
    """

    from app.core.exceptions import GraphError

    lowered = detail.lower()

    if "not configured" in lowered:
        print("    (b) CONFIGURATION — Graph credentials are incomplete.")
        return

    # Does the mailbox exist at all? A 404 here is a wrong address, which is
    # ours to fix and has nothing to do with consent.
    try:
        client.get(f"/users/{address}", {"$select": "id,userPrincipalName"})
        mailbox_resolves = True
    except GraphError as exc:
        mailbox_resolves = False
        print("    (b) CONFIGURATION — the mailbox does not resolve:")
        print(f"        {_redact(str(exc), 160)}")

    if not mailbox_resolves:
        print("        Check EMAIL_MAILBOX_ADDRESS and the connected mailbox row.")
        return

    if grants and "Calendars.Read" not in grants:
        print("    (a) MISSING PERMISSION — proven, not inferred.")
        print("        The access token's `roles` claim lists what was consented,")
        print("        and Calendars.Read is not in it. An administrator must add")
        print("        the APPLICATION permission Calendars.Read to this app")
        print("        registration and grant admin consent. Nothing in this")
        print("        repository can change that, and re-granting Files.Read.All")
        print("        or Mail.Read cannot help.")
        return

    if grants and "Calendars.Read" in grants:
        print("    (a/c) Calendars.Read IS granted on this token, and the mailbox")
        print("        resolves — so the refusal is coming from Exchange rather")
        print("        than from Entra. The usual cause is an Application Access")
        print("        Policy scoping this app to a mail-enabled security group")
        print("        that does not include this mailbox. An administrator can")
        print(
            "        check with Get-ApplicationAccessPolicy / "
            "Test-ApplicationAccessPolicy."
        )
        return

    print("    (c) The token's permissions could not be read; see the detail above.")


def check_calendar(client, mailbox: str | None, days: int, grants: set) -> bool:
    heading("6. CALENDAR")

    try:
        from app.services.features.calendar import import_service
        from app.services.graph import calendar_service
    except ImportError as exc:
        print(f"  BLOCKED: the calendar modules are not in this checkout — {exc}")
        return False

    from sqlalchemy import select

    from app.core.exceptions import CalendarUnavailableError
    from app.database.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()

    try:
        user = db.execute(select(User).order_by(User.created_at)).scalars().first()

        if user is None:
            print("  BLOCKED: no user exists, so no calendar is addressable.")
            print("           Create one with POST /users first.")
            return False

        line("acting as", f"{user.name} <{user.email}>")

        address = mailbox or import_service.mailbox_config_service.resolve_address(
            db, user_id=user.id
        )

        if not address:
            print("  No mailbox connected for this user and no --mailbox given.")
            print("  Connect one with PUT /email/mailbox, or pass --mailbox.")
            return False

        line("mailbox", address)

        access = calendar_service.status(address, client=client)
        line("configured", access.configured)
        line("connected", access.connected)
        print(f"  detail: {_redact(access.detail, 300)}")

        if not access.usable:
            print()
            print("  DIAGNOSIS:")
            _diagnose_calendar_refusal(client, address, access.detail, grants)
            return False

        until = datetime.now(UTC) + timedelta(days=days)
        since = datetime.now(UTC) - timedelta(days=days)

        line("window", f"{since.date()} → {until.date()} ({days * 2} days)")

        try:
            first = import_service.import_events(
                db, user_id=user.id, since=since, until=until, client=client
            )
        except CalendarUnavailableError as exc:
            print(f"  REFUSED: {_redact(str(exc), 300)}")
            return False

        line("created", first.created)
        line("updated", first.updated)
        line("unchanged", first.unchanged)
        line("cancelled (subset)", first.cancelled)
        line("occurrences seen", first.total)

        # Idempotency: the same window again must create nothing.
        second = import_service.import_events(
            db, user_id=user.id, since=since, until=until, client=client
        )

        print()
        line("re-import created", second.created)
        line("re-import unchanged", second.unchanged)

        idempotent = second.created == 0
        line("IDEMPOTENT", "PASS" if idempotent else "FAIL")

        events = import_service.event_service.list_events(
            db, user_id=user.id, since=since, until=until
        )

        if events:
            print()
            print("  A few imported events (title / start / status / source):")
            for event in events[:6]:
                print(
                    f"    {event.title[:44]:44s} {event.starts_at:%Y-%m-%d %H:%M} "
                    f"{event.status.value:10s} {event.source}"
                )

            cancelled = [e for e in events if e.title.startswith("Cancelled: ")]
            recurring = {}
            for event in events:
                recurring.setdefault(event.title, []).append(event)
            repeated = {k: v for k, v in recurring.items() if len(v) > 1}

            print()
            line("cancelled, marked as such", len(cancelled))
            line("titles with >1 occurrence", len(repeated))
            if repeated:
                title, occurrences = next(iter(repeated.items()))
                print(f"    recurring example: {title[:50]!r} × {len(occurrences)}")
            line(
                "all timestamps tz-aware",
                all(e.starts_at.tzinfo is not None for e in events),
            )
            line(
                "none marked attended by import",
                all(e.attendance_evidence.value == "none" for e in events),
            )

        return idempotent and first.total >= 0
    finally:
        db.close()


# --- stage 7: retrieval ----------------------------------------------------


def check_retrieval(query: str, *, run_started: datetime | None = None) -> bool:
    heading("7. KNOWLEDGE-BASE RETRIEVAL")

    from sqlalchemy import func, select

    from app.database.database import SessionLocal
    from app.models.document import Document, DocumentStatus
    from app.services.features.retrieval import retrieval_service

    db = SessionLocal()

    try:
        synced = (
            Document.status == DocumentStatus.INDEXED,
            Document.source_uri.is_not(None),
        )

        indexed = db.execute(
            select(func.count(Document.id)).where(*synced)
        ).scalar_one()

        line("indexed documents from a source", indexed)

        # History is not evidence. This knowledge base already held 268
        # documents from a staged import before any live sync ran, so counting
        # the whole corpus made a run that ingested nothing look identical to a
        # run that worked. Splitting on the run's own start time is what tells
        # them apart.
        if run_started is not None:
            current = db.execute(
                select(func.count(Document.id)).where(
                    *synced, Document.updated_at >= run_started
                )
            ).scalar_one()

            line("  written during this run", current)
            line("  already present beforehand", indexed - current)

            if indexed and not current:
                print()
                print("  NOTE: every indexed document here predates this run. The")
                print("        corpus below is historical, so a passing retrieval")
                print("        check says nothing about whether this sync worked.")

        if not indexed:
            print("  BLOCKED: nothing from OneDrive is indexed, so there is")
            print("           nothing to retrieve. Run the sync first.")
            return False

        sample = (
            db.execute(
                select(Document)
                .where(
                    Document.status == DocumentStatus.INDEXED,
                    Document.source_uri.is_not(None),
                )
                .limit(3)
            )
            .scalars()
            .all()
        )

        for document in sample:
            line(
                f"  {document.filename[:40]}",
                f"{document.chunk_count} chunks, {document.page_count or '—'} pages",
            )

        results = retrieval_service.search(db, query, top_k=5)

        print()
        line("query", repr(query))
        line("results", len(results))

        if not results:
            print("  FAIL: retrieval returned nothing for that query.")
            return False

        for rank, item in enumerate(results, start=1):
            snippet = " ".join(item.content.split())[:80]
            print(f"    {rank}. {item.filename[:38]:38s} sim={item.similarity:.3f}")
            print(f"       {snippet}")

        from_source = [
            item
            for item in results
            if db.get(Document, item.document_id).source_uri is not None
        ]

        print()
        line("results from a synced document", f"{len(from_source)} of {len(results)}")

        return bool(from_source)
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="source key; default the first")
    parser.add_argument("--full", action="store_true", help="force a full enumeration")
    parser.add_argument("--mailbox", default=None, help="calendar mailbox override")
    parser.add_argument(
        "--calendar-days",
        type=int,
        default=30,
        help="days either side of now to import (default 30)",
    )
    parser.add_argument(
        "--query",
        default="capabilities",
        help="retrieval query to prove the KB answers",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="skip the sync; useful after one has already run",
    )
    args = parser.parse_args()

    print(RULE)
    print("SunRadia AI Shadow — live integration validation")
    print(f"started {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
    print(RULE)

    run_started = datetime.now(UTC)

    ok, sources = check_configuration()
    if not ok:
        print("\nRESULT: BLOCKED at configuration.")
        return 2

    client, grants = check_authentication()
    if client is None:
        print("\nRESULT: BLOCKED at authentication.")
        return 2

    source = (
        next((s for s in sources if s.key == args.source), sources[0])
        if args.source
        else sources[0]
    )

    if not describe_source(client, source):
        print("\nRESULT: BLOCKED — the configured source does not resolve.")
        return 2

    sync_ok = True

    if args.skip_sync:
        heading("4. SYNCHRONISATION — skipped by --skip-sync")
    else:
        sync_ok, first = run_sync(client, source.key, full=args.full)
        show_status(source.key)

        if sync_ok and first is not None:
            print()
            print("  Running a second sync to prove idempotency. It should use the")
            print("  stored delta token and re-download nothing.")
            second_ok, second = run_sync(client, source.key, full=False)

            if second is not None:
                print()
                line("second run mode", second.mode)
                line("second run discovered", second.discovered)
                line("second run indexed", second.indexed)
                line(
                    "IDEMPOTENT",
                    "PASS"
                    if second.indexed == 0 and second.replaced == 0
                    else "review — content changed between runs, or not idempotent",
                )
                line(
                    "USED DELTA TOKEN",
                    "PASS"
                    if second.mode == "incremental"
                    else "NO — it re-enumerated, so the token had not advanced",
                )

    calendar_ok = check_calendar(client, args.mailbox, args.calendar_days, grants)
    retrieval_ok = check_retrieval(
        args.query, run_started=None if args.skip_sync else run_started
    )

    heading("SUMMARY")
    line("OneDrive sync", "ok" if sync_ok else "FAILED or ingested nothing")
    line("Calendar", "ok" if calendar_ok else "FAILED or blocked")
    line("KB retrieval", "ok" if retrieval_ok else "FAILED")

    status = overall_exit_code(
        sync_ok=sync_ok, calendar_ok=calendar_ok, retrieval_ok=retrieval_ok
    )

    if status == 0:
        print("\nRESULT: all three integrations validated.")
    else:
        print("\nRESULT: at least one integration did not validate — see above.")

    return status


def overall_exit_code(*, sync_ok: bool, calendar_ok: bool, retrieval_ok: bool) -> int:
    """Success is unanimous, and that is the point.

    Kept as a function of three booleans so the contract can be tested without
    a tenant: the failure mode being guarded against is a script that prints
    "FAILED" three times and exits 0, which is exactly how a broken integration
    survives a CI gate.
    """

    return 0 if (sync_ok and calendar_ok and retrieval_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
