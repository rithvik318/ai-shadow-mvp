"""Collect real mailbox and calendar data for monthly activity reports.

**Read-only.** Every call is a GET. Nothing is sent, created, modified, moved,
marked read, or deleted. No database is touched and no product code path is
invoked beyond the existing `GraphClient`, which supplies the same
client-credentials token, throttling and paging that OneDrive sync uses — there
is no second authentication mechanism here.

    cd backend
    python -m scripts.collect_activity_report_data \\
        --mailbox Robert.Keenan@sunradia.com --month 2026-07 \\
        --mailbox Robert.Keenan@sunradia.com --month 2026-08

Or, for the full set this report run needs:

    python -m scripts.collect_activity_report_data --preset sunradia-2026

Writes one JSON file per mailbox-month into `--out` (default `report_data/`),
plus a `retrieval_summary.json`. Those files contain real correspondence and
are personal data about identifiable people: keep them out of version control
(`report_data/` should be gitignored) and delete them when the reports are
done.

**Nothing secret is written or printed** — no token, no client secret, no
`webLink` (which carries a mailbox-scoped identifier). Message bodies are
converted to plain text and truncated to `--body-chars`, because a summary
needs the gist and a JSON dump of full bodies is a liability.

**Exclusions are tagged, never dropped.** A message matching `--exclude-domain`
is written with `excluded: true` and the reason, and every distinct sender is
reported in the sender inventory whether excluded or not. That keeps the
exclusion auditable and adjustable at analysis time without re-fetching a
mailbox — which matters when "identify DICE by sender, not by the word 'dice'"
is the actual requirement.

Permissions this needs, as application roles with admin consent:

- `Mail.Read` (or `Mail.ReadWrite`) — messages
- `Calendars.Read` (or `Calendars.ReadWrite`) — events

Calendar access is checked separately and its absence is recorded rather than
fatal: a mail-only report is a real report, and pretending a calendar was
analysed when it could not be read would be worse than saying so.
"""

import argparse
import json
import pathlib
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.exceptions import GraphAuthError, GraphError, SyncNotConfiguredError
from app.services.graph.client import GraphClient

# Only what a summary actually needs. `body` is fetched but truncated; `webLink`
# is deliberately not requested.
_MESSAGE_FIELDS = (
    "id,conversationId,subject,from,sender,toRecipients,ccRecipients,"
    "receivedDateTime,sentDateTime,body,bodyPreview,hasAttachments,importance,"
    "isRead,isDraft,categories,internetMessageId"
)

_EVENT_FIELDS = (
    "id,subject,start,end,isAllDay,isCancelled,organizer,attendees,location,"
    "categories,showAs,responseStatus,seriesMasterId,type,onlineMeetingProvider,"
    "bodyPreview,createdDateTime,lastModifiedDateTime"
)

# Automated senders that add volume and no signal. Reported, never silently
# discarded: they are tagged `noise` so the analysis can confirm the judgement.
_NOISE_PATTERNS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
    "notifications@",
    "notification@",
    "automated@",
)

_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

PRESET = {
    "sunradia-2026": [
        ("Robert.Keenan@sunradia.com", "2026-07"),
        ("Robert.Keenan@sunradia.com", "2026-08"),
        ("azmat.shaik@sunradia.com", "2026-07"),
        ("azmat.shaik@sunradia.com", "2026-08"),
        ("sudha.gummuluru@sunradia.com", "2026-08"),
    ]
}

# Applied to azmat's mailboxes per the report brief. Matched on the sender's
# domain, not on the word appearing anywhere in a message — a genuine email
# discussing a dice-rolling feature must not vanish.
DEFAULT_EXCLUDE_DOMAINS = ("dice.com", "dhigroupinc.com", "dhi.com")


def month_window(month: str) -> tuple[datetime, datetime, str]:
    """`2026-07` becomes [2026-07-01T00:00Z, 2026-08-01T00:00Z).

    Half-open on purpose: "through 31 July inclusive" and "before 1 August" are
    the same window, and the half-open form has no leap-second or last-
    millisecond edge to get wrong.
    """

    year, _, number = month.partition("-")
    start = datetime(int(year), int(number), 1, tzinfo=UTC)
    end_date = date(int(year), int(number), 28) + timedelta(days=4)
    end = datetime(end_date.year, end_date.month, 1, tzinfo=UTC)

    label = start.strftime("%B %Y")

    return start, end, label


def _plain_text(payload: dict, limit: int) -> str:
    """The message body as trimmed plain text.

    Graph returns HTML for most mail. Tags are stripped rather than rendered:
    this text is read by a summariser, and markup is noise that costs budget.
    """

    body = payload.get("body")
    content = ""

    if isinstance(body, dict):
        content = str(body.get("content") or "")

    if not content:
        content = str(payload.get("bodyPreview") or "")

    text = _WHITESPACE.sub(" ", _HTML_TAG.sub(" ", content)).strip()

    return text[:limit]


def _address(payload: dict | None) -> dict[str, str | None]:
    """Graph's `{emailAddress: {name, address}}`, kept structured.

    Deliberately not flattened to `"Name <address>"`. The address is what a
    domain match and a recipient field need; the name is what a report reads
    naturally. Collapsing them loses one or the other.
    """

    inner = (payload or {}).get("emailAddress") or {}
    address = inner.get("address")

    return {
        "name": inner.get("name") or None,
        "address": str(address).lower() if address else None,
    }


def _addresses(payloads: list[dict] | None) -> list[dict]:
    return [_address(item) for item in (payloads or [])]


def _domain(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None

    return address.rsplit("@", 1)[1].lower()


def _classify(
    sender: dict, exclude_domains: tuple[str, ...]
) -> tuple[bool, str | None]:
    """Should this message be excluded, and why.

    Returns `(excluded, reason)`. The message is still written out — the caller
    tags rather than drops, so a wrong call here is visible and reversible at
    analysis time instead of being an unexplained gap in a report.
    """

    address = sender.get("address") or ""
    domain = _domain(address)

    if domain and any(
        domain == item or domain.endswith(f".{item}") for item in exclude_domains
    ):
        return True, f"excluded-sender-domain:{domain}"

    if any(pattern in address for pattern in _NOISE_PATTERNS):
        return True, "automated-sender"

    return False, None


def _collect_messages(
    client: GraphClient,
    mailbox: str,
    folder: str,
    date_field: str,
    start: datetime,
    end: datetime,
    *,
    body_chars: int,
    exclude_domains: tuple[str, ...],
) -> tuple[list[dict], str | None]:
    """Every message in one folder inside the window, oldest first."""

    window = (
        f"{date_field} ge {start.strftime('%Y-%m-%dT%H:%M:%SZ')} and "
        f"{date_field} lt {end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    collected: list[dict] = []

    try:
        for payload in client.paged(
            f"/users/{mailbox}/mailFolders/{folder}/messages",
            {"$filter": window, "$select": _MESSAGE_FIELDS, "$top": 50},
        ):
            sender = _address(payload.get("from") or payload.get("sender"))
            excluded, reason = _classify(sender, exclude_domains)

            collected.append(
                {
                    "id": payload.get("id"),
                    "conversation_id": payload.get("conversationId"),
                    "subject": payload.get("subject") or "",
                    "from": sender,
                    "to": _addresses(payload.get("toRecipients")),
                    "cc": _addresses(payload.get("ccRecipients")),
                    "received_at": payload.get("receivedDateTime"),
                    "sent_at": payload.get("sentDateTime"),
                    "has_attachments": bool(payload.get("hasAttachments")),
                    "importance": payload.get("importance"),
                    "is_read": payload.get("isRead"),
                    "categories": payload.get("categories") or [],
                    "body_text": _plain_text(payload, body_chars),
                    "excluded": excluded,
                    "exclusion_reason": reason,
                }
            )
    except GraphError as exc:
        return collected, str(exc)

    # Keyed on the emitted field, not the Graph one: the payload is renamed on
    # the way out, and sorting on the Graph name silently sorts on None.
    emitted = "received_at" if date_field == "receivedDateTime" else "sent_at"
    collected.sort(key=lambda item: item.get(emitted) or "")

    return collected, None


def _collect_events(
    client: GraphClient,
    mailbox: str,
    start: datetime,
    end: datetime,
    *,
    body_chars: int,
) -> tuple[list[dict], str | None]:
    """Calendar occurrences in the window.

    `calendarView` rather than `/events`, because it expands recurring series
    into the individual occurrences that actually fall inside the month — which
    is what "meetings this month" means to a person reading a report.
    """

    collected: list[dict] = []

    try:
        for payload in client.paged(
            f"/users/{mailbox}/calendarView",
            {
                "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "$select": _EVENT_FIELDS,
                "$top": 50,
            },
        ):
            attendees = [
                {
                    **_address(item),
                    "type": item.get("type"),
                    # The only honest source of attendance. Graph reports what
                    # each attendee *responded*, not whether they turned up.
                    "response": (item.get("status") or {}).get("response"),
                    "responded_at": (item.get("status") or {}).get("time"),
                }
                for item in (payload.get("attendees") or [])
            ]

            collected.append(
                {
                    "id": payload.get("id"),
                    "series_master_id": payload.get("seriesMasterId"),
                    "type": payload.get("type"),
                    "subject": payload.get("subject") or "",
                    "start": (payload.get("start") or {}).get("dateTime"),
                    "end": (payload.get("end") or {}).get("dateTime"),
                    "timezone": (payload.get("start") or {}).get("timeZone"),
                    "is_all_day": bool(payload.get("isAllDay")),
                    "is_cancelled": bool(payload.get("isCancelled")),
                    "organizer": _address(payload.get("organizer")),
                    "attendees": attendees,
                    "location": (payload.get("location") or {}).get("displayName"),
                    "show_as": payload.get("showAs"),
                    # This mailbox owner's own response to the invitation.
                    "my_response": (payload.get("responseStatus") or {}).get(
                        "response"
                    ),
                    "categories": payload.get("categories") or [],
                    "online_provider": payload.get("onlineMeetingProvider"),
                    "created_at": payload.get("createdDateTime"),
                    "last_modified_at": payload.get("lastModifiedDateTime"),
                    "body_text": _plain_text(payload, min(body_chars, 1000)),
                }
            )
    except GraphAuthError as exc:
        return [], (
            f"Calendar not readable: {exc} This usually means the application "
            "has Mail permissions but not Calendars.Read."
        )
    except GraphError as exc:
        return collected, str(exc)

    collected.sort(key=lambda item: item.get("start") or "")

    return collected, None


def _sender_inventory(messages: list[dict]) -> list[dict]:
    """Distinct senders with counts, so an exclusion can be checked by eye."""

    counts: dict[str, dict[str, Any]] = {}

    for message in messages:
        address = message["from"].get("address") or "(unknown)"
        entry = counts.setdefault(
            address,
            {
                "address": address,
                "name": message["from"].get("name"),
                "domain": _domain(address),
                "count": 0,
                "excluded": message["excluded"],
                "exclusion_reason": message["exclusion_reason"],
            },
        )
        entry["count"] += 1

    return sorted(counts.values(), key=lambda item: -item["count"])


def collect(
    client: GraphClient,
    mailbox: str,
    month: str,
    *,
    body_chars: int,
    exclude_domains: tuple[str, ...],
) -> dict:
    start, end, label = month_window(month)

    received, received_error = _collect_messages(
        client,
        mailbox,
        "inbox",
        "receivedDateTime",
        start,
        end,
        body_chars=body_chars,
        exclude_domains=exclude_domains,
    )
    sent, sent_error = _collect_messages(
        client,
        mailbox,
        "sentitems",
        "sentDateTime",
        start,
        end,
        body_chars=body_chars,
        exclude_domains=(),  # Never exclude the person's own outbound mail.
    )
    events, calendar_error = _collect_events(
        client, mailbox, start, end, body_chars=body_chars
    )

    limitations = [
        item
        for item in (
            f"Inbox: {received_error}" if received_error else None,
            f"Sent items: {sent_error}" if sent_error else None,
            f"Calendar: {calendar_error}" if calendar_error else None,
        )
        if item
    ]

    excluded_count = sum(1 for item in received if item["excluded"])

    return {
        "mailbox": mailbox,
        "month": month,
        "month_label": label,
        "window_start_utc": start.isoformat(),
        "window_end_utc_exclusive": end.isoformat(),
        "collected_at_utc": datetime.now(UTC).isoformat(),
        "exclude_domains": list(exclude_domains),
        "counts": {
            "received_total": len(received),
            "received_excluded": excluded_count,
            "received_for_analysis": len(received) - excluded_count,
            "sent": len(sent),
            "calendar_events": len(events),
            "conversations": len(
                {
                    item["conversation_id"]
                    for item in received + sent
                    if item.get("conversation_id")
                }
            ),
        },
        "limitations": limitations,
        "sender_inventory": _sender_inventory(received),
        "received": received,
        "sent": sent,
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailbox", action="append", default=[])
    parser.add_argument("--month", action="append", default=[])
    parser.add_argument("--preset", choices=sorted(PRESET))
    parser.add_argument("--out", default="report_data")
    parser.add_argument("--body-chars", type=int, default=3000)
    parser.add_argument(
        "--exclude-domain",
        action="append",
        default=None,
        help=(
            "Sender domain to tag as excluded. Repeatable. Defaults to the "
            "DICE domains; pass explicitly to override."
        ),
    )
    arguments = parser.parse_args()

    if arguments.preset:
        pairs = PRESET[arguments.preset]
    else:
        if len(arguments.mailbox) != len(arguments.month):
            print("Give --mailbox and --month the same number of times.")
            return 2
        pairs = list(zip(arguments.mailbox, arguments.month, strict=True))

    if not pairs:
        print("Nothing to collect. Use --preset or --mailbox/--month.")
        return 2

    exclude_domains = tuple(arguments.exclude_domain or DEFAULT_EXCLUDE_DOMAINS)

    try:
        client = GraphClient.from_settings()
    except SyncNotConfiguredError as exc:
        print(f"Not configured: {exc}")
        return 2

    out = pathlib.Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)

    print("Retrieval summary")
    print("=" * 78)
    print(
        f"{'Mailbox':<34}{'Month':<9}{'Recv':>6}{'Excl':>6}{'Sent':>6}"
        f"{'Events':>8}{'Conv':>6}"
    )
    print("-" * 78)

    summaries = []

    for mailbox, month in pairs:
        # DICE exclusion applies to azmat's mailbox only, per the brief.
        applies = exclude_domains if mailbox.lower().startswith("azmat.") else ()

        try:
            bundle = collect(
                client,
                mailbox,
                month,
                body_chars=arguments.body_chars,
                exclude_domains=applies,
            )
        except GraphAuthError as exc:
            print(f"{mailbox:<34}{month:<9}  DENIED: {exc}")
            summaries.append(
                {"mailbox": mailbox, "month": month, "error": f"denied: {exc}"}
            )
            continue
        except GraphError as exc:
            print(f"{mailbox:<34}{month:<9}  FAILED: {exc}")
            summaries.append(
                {"mailbox": mailbox, "month": month, "error": f"failed: {exc}"}
            )
            continue

        counts = bundle["counts"]
        print(
            f"{mailbox:<34}{month:<9}{counts['received_total']:>6}"
            f"{counts['received_excluded']:>6}{counts['sent']:>6}"
            f"{counts['calendar_events']:>8}{counts['conversations']:>6}"
        )

        for limitation in bundle["limitations"]:
            print(f"    ! {limitation}")

        name = f"{mailbox.split('@')[0].replace('.', '_')}_{month}.json"
        (out / name).write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        summaries.append(
            {
                "mailbox": mailbox,
                "month": month,
                "window": [
                    bundle["window_start_utc"],
                    bundle["window_end_utc_exclusive"],
                ],
                "counts": counts,
                "limitations": bundle["limitations"],
                "file": name,
            }
        )

    (out / "retrieval_summary.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )

    print("-" * 78)
    print(f"Written to {out.resolve()}")
    print(
        "\nThese files contain real correspondence about identifiable people. "
        "Keep them out of git and delete them once the reports are finished."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
