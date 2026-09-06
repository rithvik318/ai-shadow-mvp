"""What actually happened in one person's mailbox over one period.

The email digest is a different question from the weekly work report, which is
why it is a different service and a different report type. The work report asks
"what do I still have to do"; the digest asks "what came in, who was it from,
what did I already decide about it, and what did I send". One needs tasks and
no mailbox; the other needs a mailbox and is impossible without one.

Four refusals shape this module, and each exists because the obvious
alternative produces a plausible lie:

**No mailbox means no digest — not an empty one.** `build()` raises
`EmailProviderNotConfiguredError` rather than returning zero counts. Zero
counts assert that a mailbox was read and held nothing; nothing was read. The
persistence layer records that refusal as an `unavailable` report with the
reason in words, which is a different row from a genuinely quiet week.

**Nothing is inferred about a message that was never triaged.** A digest counts
what triage decided *where triage ran*, and reports the untriaged remainder as
untriaged. Running the classifier over a month of mail to fill in a summary
would be a model call per message, silently, on a schedule.

**The window is the period's, not the provider's page.** Messages are filtered
on `received_at` against the half-open period. A message with no timestamp is
excluded and counted as such rather than being swept into whichever period is
being generated.

**Truncation is reported, never hidden.** The provider boundary offers
`list_messages(limit=...)` and no date range — a deliberate narrowness, since a
date filter is expressible by exactly one vendor's query language. So the
digest asks for a bounded page and, if the oldest message it received still
falls inside the period, says the window may be incomplete instead of
presenting a partial count as a total. Widening the boundary to take a date
range is the right fix and belongs in its own change; claiming completeness
this build cannot guarantee is not.

Deterministic given the same inputs: no model call, no prompt, no randomness.
The digest is a count and a grouping, which is what makes it safe to generate
on a schedule.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.email import EmailAssessment, EmailDraft, EmailDraftStatus
from app.services.email.provider.base import EmailMessage, EmailProvider
from app.services.features.email import mailbox_config_service
from app.services.features.reports.period import Period

logger = logging.getLogger(__name__)

#: How many messages one digest asks the provider for. Generous relative to a
#: week of ordinary correspondence and bounded so a scheduled run over every
#: user cannot turn into an unbounded crawl. When it binds, the digest says so.
DEFAULT_MESSAGE_LIMIT = 400

UNTRIAGED = "untriaged"

TRUNCATED_DETAIL = (
    "The mailbox returned as many messages as this digest asks for, and the "
    "oldest of them still falls inside the period — so there may be earlier "
    "messages in this window that are not counted here."
)


@dataclass(frozen=True)
class Correspondent:
    """One person, and how much of the period they account for."""

    address: str
    name: str | None
    message_count: int


@dataclass(frozen=True)
class DigestMessage:
    """One message as the digest shows it, with triage's verdict where there is one."""

    message_id: str
    subject: str
    sender_name: str | None
    sender_address: str | None
    received_at: datetime | None
    #: The category triage assigned, or `untriaged`. Never guessed.
    category: str
    priority: str | None
    summary: str | None
    needs_reply: bool


@dataclass(frozen=True)
class EmailDigest:
    """One person's correspondence over one period."""

    user_id: uuid.UUID
    period: Period
    generated_at: datetime
    mailbox: str | None

    received_count: int = 0
    sent_count: int = 0
    triaged_count: int = 0
    untriaged_count: int = 0
    needs_reply_count: int = 0
    follow_up_count: int = 0
    undated_count: int = 0

    by_category: dict[str, int] = field(default_factory=dict)
    by_priority: dict[str, int] = field(default_factory=dict)
    top_correspondents: list[Correspondent] = field(default_factory=list)
    needs_reply: list[DigestMessage] = field(default_factory=list)
    highlights: list[DigestMessage] = field(default_factory=list)

    #: True when the provider page may not reach back to the period's start.
    truncated: bool = False
    truncation_detail: str | None = None

    @property
    def is_quiet(self) -> bool:
        """A period in which the mailbox was read and genuinely held nothing.

        Distinct from `unavailable`, which is what a period with no mailbox
        records. Both render as "nothing here"; only one of them is a fact
        about the mail.
        """

        return self.received_count == 0 and self.sent_count == 0


def _sender_key(message: EmailMessage) -> tuple[str, str | None] | None:
    if message.sender is None or not message.sender.address:
        return None

    return message.sender.address.strip().lower(), message.sender.name


def _in_period(message: EmailMessage, period: Period) -> bool:
    return period.contains(message.received_at)


def _sent_count(db: Session, *, user_id: uuid.UUID, period: Period) -> int:
    """Messages this person actually sent in the window.

    Counted from drafts that carry a `sent_at`, which is written only after a
    provider returned a receipt. Nothing here counts an approved-but-unsent
    draft as sent — the whole approve-then-send path exists to keep those two
    distinguishable.
    """

    return int(
        db.execute(
            select(func.count())
            .select_from(EmailDraft)
            .where(
                EmailDraft.user_id == user_id,
                EmailDraft.status == EmailDraftStatus.SENT,
                EmailDraft.sent_at.is_not(None),
                # Half-open, matching `Period.contains`, so a message sent at
                # exactly midnight belongs to one period rather than two.
                EmailDraft.sent_at >= period.start,
                EmailDraft.sent_at < period.end,
            )
        ).scalar_one()
    )


def _assessments(
    db: Session, *, user_id: uuid.UUID, provider: str, message_ids: list[str]
) -> dict[str, EmailAssessment]:
    if not message_ids:
        return {}

    rows = (
        db.execute(
            select(EmailAssessment).where(
                EmailAssessment.user_id == user_id,
                EmailAssessment.provider == provider,
                EmailAssessment.provider_message_id.in_(message_ids),
            )
        )
        .scalars()
        .all()
    )

    return {row.provider_message_id: row for row in rows}


def _describe(
    message: EmailMessage, assessment: EmailAssessment | None
) -> DigestMessage:
    return DigestMessage(
        message_id=message.message_id,
        subject=message.subject or "(no subject)",
        sender_name=message.sender.name if message.sender else None,
        sender_address=message.sender.address if message.sender else None,
        received_at=message.received_at,
        category=str(assessment.category) if assessment else UNTRIAGED,
        priority=str(assessment.priority) if assessment else None,
        summary=assessment.summary if assessment else None,
        needs_reply=bool(assessment and str(assessment.category) == "needs_reply"),
    )


def build(
    db: Session,
    *,
    user_id: uuid.UUID,
    period: Period,
    now: datetime | None = None,
    limit: int = DEFAULT_MESSAGE_LIMIT,
    provider: EmailProvider | None = None,
) -> EmailDigest:
    """The digest for one person over one period.

    Raises `EmailProviderNotConfiguredError` when this user has no mailbox. The
    caller decides what to do with that; what it must not do is substitute an
    empty digest, which would read as "a quiet week" for somebody who has never
    connected an inbox.
    """

    moment = now or datetime.now(UTC)
    mailbox = provider or mailbox_config_service.provider_for(db, user_id=user_id)
    address = mailbox_config_service.resolve_address(db, user_id=user_id)

    fetched = mailbox.list_messages(limit=limit)
    in_window = [message for message in fetched if _in_period(message, period)]

    # The page bound bit only if it was reached *and* the oldest message in it
    # is still inside the window — a full page that already reaches past the
    # period's start has seen everything the period contains.
    dated = [m.received_at for m in fetched if m.received_at is not None]
    oldest = min((d.astimezone(UTC) for d in dated), default=None)
    truncated = len(fetched) >= limit and oldest is not None and oldest >= period.start

    assessments = _assessments(
        db,
        user_id=user_id,
        provider=mailbox.name,
        message_ids=[message.message_id for message in in_window],
    )

    described = [
        _describe(message, assessments.get(message.message_id)) for message in in_window
    ]

    categories = Counter(item.category for item in described)
    priorities = Counter(
        item.priority for item in described if item.priority is not None
    )

    senders: Counter[tuple[str, str | None]] = Counter()
    for message in in_window:
        key = _sender_key(message)
        if key is not None:
            senders[key] += 1

    follow_ups = sum(
        1
        for message in in_window
        if (found := assessments.get(message.message_id)) is not None
        and found.follow_up_recommended
    )

    needs_reply = [item for item in described if item.needs_reply]

    # Highlights are what triage already called urgent or high priority. A
    # digest that ranked messages by its own opinion would be a second
    # classifier disagreeing with the first.
    highlights = [
        item
        for item in described
        if item.category == "urgent" or item.priority == "high"
    ]

    digest = EmailDigest(
        user_id=user_id,
        period=period,
        generated_at=moment,
        mailbox=address,
        received_count=len(described),
        sent_count=_sent_count(db, user_id=user_id, period=period),
        triaged_count=sum(1 for item in described if item.category != UNTRIAGED),
        untriaged_count=categories.get(UNTRIAGED, 0),
        needs_reply_count=len(needs_reply),
        follow_up_count=follow_ups,
        undated_count=sum(1 for message in fetched if message.received_at is None),
        by_category=dict(sorted(categories.items())),
        by_priority=dict(sorted(priorities.items())),
        top_correspondents=[
            Correspondent(address=address_, name=name, message_count=count)
            for (address_, name), count in senders.most_common(10)
        ],
        needs_reply=sorted(
            needs_reply, key=lambda item: item.received_at or period.start, reverse=True
        ),
        highlights=sorted(
            highlights, key=lambda item: item.received_at or period.start, reverse=True
        ),
        truncated=truncated,
        truncation_detail=TRUNCATED_DETAIL if truncated else None,
    )

    logger.info(
        "email_digest_built",
        extra={
            "user_id": str(user_id),
            "period": period.key,
            "kind": str(period.kind),
            "received": digest.received_count,
            "truncated": digest.truncated,
        },
    )

    return digest
