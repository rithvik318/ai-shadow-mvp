"""The evidence a report is written from: real messages, with their bodies.

This module exists because the reporting path had no access to correspondence.
`digest_service` produces an `EmailDigest` — counts, a category tally and a
list of top correspondents — and `activity_report` was assembled from that. By
the time a report was written, every body and every thread relationship had
already been discarded, so the best possible report was a summary of counts.
That is the whole reason the output was five lines long.

What a report needs, and what this returns, is the opposite: each message kept
whole, with the identifiers that let a later stage cite it and the thread id
that lets conversations be reassembled.

Two sources, one type. `from_collected` reads the JSON that
`scripts/collect_activity_report_data.py` writes, which is how the pipeline can
be exercised against real correspondence with no tenant and no network — every
test below this line runs on it. `from_provider` is the live path: it reads the
inbox and the sent folder through the provider boundary, bounded to the period
being reported on rather than filtered down to it afterwards.

**Truncation is recorded, never silent.** A mailbox larger than the ceiling
produces a `Correspondence` whose `truncated` flag is set and whose
`limitations` say so in words, and every downstream stage carries that through
to the report header. A report that quietly described two thirds of a month
would be worse than one that admits its window.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from app.services.email.provider.base import EmailMessage, EmailProvider
from app.services.features.reports.period import Period

logger = logging.getLogger(__name__)

#: How many messages one report will read per direction before it stops and
#: says so. Comfortably above a heavy month — the largest mailbox measured in
#: this corpus received 947 in August — and bounded so a scheduled run over
#: every user cannot turn into an unbounded crawl.
MAX_MESSAGES_PER_DIRECTION = 2_000

#: Bodies are kept, but not without limit: a report's analysis stage reads
#: thousands of them, and a single 200KB newsletter would spend a whole model
#: budget saying nothing. Enough to carry a request, an answer and its
#: reasoning.
MAX_BODY_CHARS = 4_000

#: The folder a mailbox keeps its outgoing mail in. The provider boundary
#: offers `list_messages(folder=...)` and nothing narrower, so this is how the
#: outbound half of a period is read. Named here rather than at the call site
#: because a second provider would map its own name onto this one word.
SENT_FOLDER = "sentitems"


class Direction(StrEnum):
    """Who wrote it. The distinction the whole report turns on.

    "The owner replied" and "nobody replied" are different facts about a
    thread, and they cannot be told apart without this.
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"


@dataclass(frozen=True)
class Participant:
    """One person on a message."""

    address: str
    name: str | None = None

    @property
    def domain(self) -> str:
        _, _, domain = self.address.partition("@")

        return domain.lower()

    @property
    def display(self) -> str:
        return self.name or self.address


@dataclass(frozen=True)
class Message:
    """One message, kept whole enough to reason about and to cite."""

    message_id: str
    thread_id: str
    subject: str
    body: str
    sender: Participant | None
    recipients: list[Participant]
    cc: list[Participant]
    occurred_at: datetime | None
    direction: Direction
    has_attachments: bool = False
    importance: str | None = None

    @property
    def participants(self) -> list[Participant]:
        people = [*self.recipients, *self.cc]

        if self.sender is not None:
            people.insert(0, self.sender)

        return people


@dataclass(frozen=True)
class Correspondence:
    """Every message in one mailbox over one period, plus what is missing."""

    mailbox: str
    owner_name: str | None
    period: Period
    messages: list[Message] = field(default_factory=list)
    truncated: bool = False
    limitations: list[str] = field(default_factory=list)

    @property
    def inbound(self) -> list[Message]:
        return [m for m in self.messages if m.direction is Direction.INBOUND]

    @property
    def outbound(self) -> list[Message]:
        return [m for m in self.messages if m.direction is Direction.OUTBOUND]

    @property
    def thread_ids(self) -> set[str]:
        return {message.thread_id for message in self.messages}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _participant(payload: object) -> Participant | None:
    if not isinstance(payload, dict):
        return None

    address = _text(payload.get("address")).strip()

    if not address:
        return None

    return Participant(address=address.lower(), name=_text(payload.get("name")) or None)


def _participants(payload: object) -> list[Participant]:
    if not isinstance(payload, list):
        return []

    found = [_participant(item) for item in payload]

    return [person for person in found if person is not None]


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        # Graph writes `Z`; `fromisoformat` wants an offset before 3.11's
        # relaxation, and being explicit costs nothing.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _clean_body(raw: object) -> str:
    """The body as plain text, bounded.

    The collector already converts HTML to text, but it leaves the entities a
    mail client emits — `&nbsp;` runs through this corpus — and those become
    noise in an analysis prompt and in any quoted evidence.
    """

    import html

    text = html.unescape(_text(raw)).replace(" ", " ")
    collapsed = "\n".join(line.rstrip() for line in text.splitlines())

    return collapsed.strip()[:MAX_BODY_CHARS]


#: Graph returns most mail as HTML. These are enough to recognise it; a plain
#: text body that merely contains "<name@example.com>" is left alone.
_HTML_MARKER = re.compile(r"<\s*(html|body|div|p|br|table|span|head)\b", re.IGNORECASE)
_HTML_INVISIBLE = re.compile(
    r"<\s*(style|script|head)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL
)
_HTML_BREAK = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


def _html_to_text(raw: str) -> str:
    """HTML mail as text: markup removed, line breaks kept.

    The same treatment the collector gives bodies before they are stored, so a
    live report reads text of the shape the collected corpus holds. Stylesheets
    and scripts are dropped whole — their contents are not correspondence.
    Plain text passes through untouched.
    """

    if not _HTML_MARKER.search(raw):
        return raw

    text = _HTML_INVISIBLE.sub(" ", raw)
    text = _HTML_BREAK.sub("\n", text)

    return _HTML_TAG.sub(" ", text)


def _fallback_thread_id(payload: dict, message_id: str) -> str:
    """A thread id for a message whose provider gave none.

    Grouping on the normalised subject is a weaker claim than a conversation id
    and is used only where the real one is absent — otherwise two unrelated
    messages both called "Invoice" would be read as one conversation. Falling
    back to the message's own id keeps a thread of one rather than merging
    everything subjectless into a single phantom conversation.
    """

    subject = _text(payload.get("subject")).strip().lower()

    for prefix in ("re:", "fw:", "fwd:"):
        while subject.startswith(prefix):
            subject = subject[len(prefix) :].strip()

    return f"subject:{subject}" if subject else f"message:{message_id}"


def _from_payload(payload: dict, *, mailbox: str) -> Message | None:
    message_id = _text(payload.get("id")).strip()

    if not message_id:
        return None

    sender = _participant(payload.get("from"))
    direction = (
        Direction.OUTBOUND
        if sender is not None and sender.address == mailbox
        else Direction.INBOUND
    )

    thread_id = _text(payload.get("conversation_id")).strip() or _fallback_thread_id(
        payload, message_id
    )

    return Message(
        message_id=message_id,
        thread_id=thread_id,
        subject=_text(payload.get("subject")).strip() or "(no subject)",
        body=_clean_body(payload.get("body_text")),
        sender=sender,
        recipients=_participants(payload.get("to")),
        cc=_participants(payload.get("cc")),
        # A sent message's `received_at` is when it landed for the recipient;
        # `sent_at` is when the owner acted, which is the date a report means.
        occurred_at=(
            _moment(payload.get("sent_at"))
            if direction is Direction.OUTBOUND
            else _moment(payload.get("received_at"))
        )
        or _moment(payload.get("received_at"))
        or _moment(payload.get("sent_at")),
        direction=direction,
        has_attachments=bool(payload.get("has_attachments")),
        importance=_text(payload.get("importance")) or None,
    )


def from_collected(payload: dict, *, period: Period | None = None) -> Correspondence:
    """Read one mailbox-month written by `collect_activity_report_data`.

    `period` narrows the file to a sub-window — which is what makes a weekly
    report out of a monthly collection, and therefore what makes month-to-date
    weekly segmentation possible without re-fetching anything. Omit it and the
    file's own window is used.

    Messages the collector tagged `excluded` are dropped here with their reason
    carried into `limitations`, so an exclusion decided at collection time
    stays visible at analysis time rather than silently shrinking the corpus.
    """

    mailbox = _text(payload.get("mailbox")).strip().lower()
    window = period or _collected_window(payload)

    messages: list[Message] = []
    excluded = 0

    for direction_key in ("received", "sent"):
        for item in payload.get(direction_key) or []:
            if not isinstance(item, dict):
                continue

            if item.get("excluded"):
                excluded += 1
                continue

            message = _from_payload(item, mailbox=mailbox)

            if message is None:
                continue

            if window is not None and not window.contains(message.occurred_at):
                continue

            messages.append(message)

    messages.sort(key=lambda m: m.occurred_at or datetime.min.replace(tzinfo=UTC))

    limitations = [
        item for item in (payload.get("limitations") or []) if isinstance(item, str)
    ]

    if excluded:
        limitations.append(
            f"{excluded} message(s) were tagged as excluded senders at collection "
            "time and are not analysed here."
        )

    return Correspondence(
        mailbox=mailbox,
        owner_name=_owner_name(messages, mailbox),
        period=window,
        messages=messages,
        limitations=limitations,
    )


def _collected_window(payload: dict) -> Period | None:
    from app.services.features.reports.period import range_between

    start = _moment(payload.get("window_start_utc"))
    end = _moment(payload.get("window_end_utc_exclusive"))

    if start is None or end is None or end <= start:
        return None

    return range_between(start, end)


def _owner_name(messages: list[Message], mailbox: str) -> str | None:
    """The owner's display name, taken from a message they sent."""

    for message in messages:
        if message.direction is Direction.OUTBOUND and message.sender is not None:
            if message.sender.name:
                return message.sender.name

    for message in messages:
        for person in message.recipients:
            if person.address == mailbox and person.name:
                return person.name

    return None


def _from_provider_message(message: EmailMessage, *, mailbox: str) -> Message | None:
    sender = (
        Participant(
            address=message.sender.address.lower(), name=message.sender.name or None
        )
        if message.sender is not None
        else None
    )
    direction = (
        Direction.OUTBOUND
        if sender is not None and sender.address == mailbox
        else Direction.INBOUND
    )

    return Message(
        message_id=message.message_id,
        thread_id=message.thread_id
        or _fallback_thread_id({"subject": message.subject}, message.message_id),
        subject=(message.subject or "").strip() or "(no subject)",
        # A windowed listing carries the body; the preview is the fallback for
        # a message Graph returned without one. A preview is thin evidence but
        # it is evidence, and refusing it would lose the message entirely.
        body=_clean_body(_html_to_text(message.body or message.snippet or "")),
        sender=sender,
        recipients=[
            Participant(address=p.address.lower(), name=p.name or None)
            for p in message.to_recipients
        ],
        cc=[
            Participant(address=p.address.lower(), name=p.name or None)
            for p in message.cc_recipients
        ],
        occurred_at=message.received_at,
        direction=direction,
        has_attachments=bool(getattr(message, "attachments", None)),
    )


def from_provider(
    provider: EmailProvider,
    *,
    mailbox: str,
    period: Period,
    limit: int = MAX_MESSAGES_PER_DIRECTION,
) -> Correspondence:
    """Read a live mailbox for one period.

    Both directions come from `list_messages`: the inbox, and then the sent
    folder. There is no `list_sent_messages` on `EmailProvider` and adding one
    would put a second listing method on every future provider to express what
    `folder` already expresses.

    The period is passed to the provider rather than applied afterwards, so a
    report reads the window it is about and nothing else. It is applied again
    here on the way through, because `since`/`until` are a request a provider
    may only partly honour and a message outside the window must not reach a
    report either way.

    A provider that returns the full `limit` is assumed to have more, and the
    report says so in its header. Describing a partial corpus as a whole one is
    the single thing this module must not do.
    """

    messages: list[Message] = []
    truncated = False

    for folder, direction in (
        (None, Direction.INBOUND),
        (SENT_FOLDER, Direction.OUTBOUND),
    ):
        # The window goes to the provider. Asking for the newest messages and
        # discarding the ones outside the period is how this used to work, and
        # it reads a whole mailbox to describe a week of it — and returns
        # nothing at all for a period older than one page.
        fetched = list(
            provider.list_messages(
                limit=limit,
                folder=folder,
                since=period.start,
                until=period.end,
            )
        )

        if len(fetched) >= limit:
            truncated = True

        for item in fetched:
            message = _from_provider_message(item, mailbox=mailbox.lower())

            if message is None:
                continue

            if direction is Direction.OUTBOUND:
                # Everything in the sent folder left this mailbox, whatever the
                # From header says. Sent-on-behalf and shared mailboxes make the
                # sender an unreliable way to decide that here.
                message = replace(message, direction=Direction.OUTBOUND)

            if not period.contains(message.occurred_at):
                continue

            messages.append(message)

    seen: set[str] = set()
    unique: list[Message] = []

    for message in sorted(
        messages, key=lambda m: m.occurred_at or datetime.min.replace(tzinfo=UTC)
    ):
        if message.message_id in seen:
            continue

        seen.add(message.message_id)
        unique.append(message)

    limitations: list[str] = []

    if truncated:
        limitations.append(
            f"The mailbox returned the maximum this report reads ({limit} per "
            "direction) for this period, so there may be messages in it that "
            "are not counted or analysed here."
        )

    return Correspondence(
        mailbox=mailbox.lower(),
        owner_name=_owner_name(unique, mailbox.lower()),
        period=period,
        messages=unique,
        truncated=truncated,
        limitations=limitations,
    )
