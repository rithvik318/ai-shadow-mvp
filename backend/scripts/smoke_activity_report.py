"""Deployment smoke test: one real activity report, end to end, through HTTP.

Runs against a *running* backend (`--base-url`) with the deployment's own
`.env`, so the path exercised is the production one: Graph mailbox →
correspondence → threads → analysis → synthesis → composition → quality →
`activity_report` row → cached second read.

    python -m scripts.smoke_activity_report --base-url http://127.0.0.1:8000 \
        --user-email someone@sunradia.com --start 2026-09-01 --end 2026-09-30

Use a CLOSED window (its end before today) so the second GET must be served
from storage. The first request calls POST (the Regenerate route), so it
replaces any stored report for that exact window.

Prints counts, timings, ids and PASS/FAIL lines only — never a subject, a
body, an address or a credential — so the output can be pasted anywhere.
Exit code 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx

from app.database.session import SessionLocal
from app.services.email.provider.outlook_provider import OutlookEmailProvider
from app.services.features.email import mailbox_config_service
from app.services.features.reports import activity_store
from app.services.features.reports import correspondence as report_correspondence
from app.services.features.reports.period import range_between

PREVIEW_CHARS = 255
#: Evidence is printed as backticked message ids. Graph ids are long; the
#: length floor keeps other inline code out of the "is it real" check.
_EVIDENCE = re.compile(r"`([^`\s]+)`")
_GRAPH_ID_MIN = 40

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((ok, label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    if args.end >= datetime.now(UTC).date():
        print("Use a closed window (end before today) so caching can be verified.")
        return 2

    http = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout)

    # 1. The server is up.
    if not check(http.get("/health").status_code == 200, "GET /health is 200"):
        return 1

    # 2. The user, and their mailbox connection.
    users = http.get("/users").json().get("items", [])
    match = [u for u in users if u.get("email", "").lower() == args.user_email.lower()]

    if not check(len(match) == 1, "user found by email"):
        return 1

    user_id = match[0]["id"]
    headers = {"X-User-ID": user_id}
    status = http.get("/email/provider/status", headers=headers).json()

    if not check(
        bool(status.get("connected")),
        f"mailbox connected (Graph token + Mail.Read) — detail: {status.get('detail')}",
    ):
        return 1

    # 3. Read the mailbox the way the report does, in-process, with this .env.
    period = range_between(
        datetime(args.start.year, args.start.month, args.start.day, tzinfo=UTC),
        datetime(args.end.year, args.end.month, args.end.day, tzinfo=UTC)
        + timedelta(days=1),
    )
    db = SessionLocal()

    try:
        provider = mailbox_config_service.provider_for(db, user_id=uuid.UUID(user_id))
        mailbox = mailbox_config_service.resolve_address(db, user_id=uuid.UUID(user_id))
        started = time.monotonic()
        corr = report_correspondence.from_provider(
            provider, mailbox=mailbox, period=period
        )
        fetch_s = time.monotonic() - started
        ids = {m.message_id for m in corr.messages}
        lengths = [len(m.body) for m in corr.messages]
        print(
            f"      fetched {len(corr.messages)} messages "
            f"({len(corr.inbound)} in / {len(corr.outbound)} out) in {fetch_s:.1f}s; "
            f"truncated={corr.truncated}; longest body={max(lengths, default=0)} chars"
        )
        check(len(corr.messages) > 0, "emails retrieved from Graph")
        check(len(corr.outbound) > 0, "sent items retrieved (outbound > 0)")
        check(
            any(n > PREVIEW_CHARS for n in lengths),
            f"full bodies present (a body > {PREVIEW_CHARS} chars)",
        )

        # Drafts in the window, read straight from Graph's Drafts folder.
        if isinstance(provider, OutlookEmailProvider):
            drafts = provider._call(
                f"/users/{mailbox}/mailFolders/drafts/messages",
                {
                    "$select": "id,isDraft",
                    "$top": 200,
                    "$filter": (
                        f"lastModifiedDateTime ge {period.start:%Y-%m-%dT%H:%M:%SZ}"
                    ),
                },
            ).get("value", [])
            draft_ids = {d["id"] for d in drafts}
            print(f"      drafts modified since window start: {len(draft_ids)}")
            check(not (draft_ids & ids), "no draft is among the report inputs")
    finally:
        db.close()

    params = {"start": args.start.isoformat(), "end": args.end.isoformat()}

    # 4. Generate (POST = Regenerate, so the run is a real generation).
    started = time.monotonic()
    response = http.post("/reports/activity", params=params, headers=headers)
    gen_s = time.monotonic() - started
    print(f"      POST /reports/activity -> {response.status_code} in {gen_s:.1f}s")

    if response.status_code == 202:
        print("      another generation holds the lock; wait and rerun.")

    if not check(response.status_code == 200, "generation returned 200"):
        print(f"      body: {response.text[:300]}")
        return 1

    report = response.json()
    markdown = report.get("markdown", "")
    cited = set(_EVIDENCE.findall(markdown))
    print(
        f"      source={report.get('source')} words={report.get('word_count')} "
        f"threads={report.get('thread_count')} "
        f"substantive={report.get('substantive_threads')} "
        f"narrative={report.get('narrative')} quality_ok={report.get('quality_ok')}"
    )
    for note in (report.get("limitations") or []) + (report.get("quality_notes") or []):
        print(f"      note: {note[:160]}")

    check(report.get("source") == "generated", "report was freshly generated")
    check(
        report.get("messages_analysed", 0) == len(corr.messages),
        "same messages",
    )
    check(bool(report.get("narrative")), "LLM narrative produced (OpenRouter worked)")
    check(bool(cited & ids), f"cites real message ids ({len(cited & ids)})")
    check(
        not {c for c in cited if len(c) >= _GRAPH_ID_MIN} - ids,
        "every cited id is a real message in the window",
    )

    # 5. Persisted.
    db = SessionLocal()
    try:
        row = activity_store.find(db, user_id=uuid.UUID(user_id), period=period)
        check(
            row is not None and row.status.value == "complete",
            "activity_report row stored with status complete",
        )
    finally:
        db.close()

    # 6. Second read is cached.
    started = time.monotonic()
    again = http.get("/reports/activity", params=params, headers=headers)
    cache_s = time.monotonic() - started
    print(f"      GET /reports/activity -> {again.status_code} in {cache_s:.2f}s")
    check(
        again.status_code == 200 and again.json().get("source") == "stored",
        "second GET served from storage without regenerating",
    )
    check(again.json().get("markdown") == markdown, "cached report identical")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
