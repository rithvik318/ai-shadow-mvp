"""Conversations, rebuilt from the messages that make them up.

A report about a mailbox is really a report about its conversations. "Twelve
messages from Sebastian Murry" says nothing; "a quote came in over budget, two
more were requested, one arrived and the owner has not yet answered" is the
same twelve messages read as a thread, and only the second is worth a
manager's time.

Everything here is derived arithmetically from the messages — who took part,
when it started and stopped, which side spoke last, whether the owner replied
at all. None of it is inferred and none of it needs a model. What a model is
later asked for is the part arithmetic cannot supply: what the conversation was
*about*, and what it means. Keeping that boundary sharp is what makes the
analysis stage auditable, because every structural claim in the final report
can be recomputed from the rows.

`ThreadShape` is the one judgement made here, and it is deliberately about
*form* rather than outcome. "The last word was inbound and the owner never
answered" is a fact about the transcript. "The client is waiting on us" is a
conclusion about the business, and that belongs to the stage that reads the
words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.services.features.reports.classification import (
    MessageClass,
    classify,
    organisation_of,
)
from app.services.features.reports.correspondence import (
    Correspondence,
    Direction,
    Message,
    Participant,
)

#: A thread needs at least this many substantive messages, or one outbound
#: message, to be offered to the analysis stage. A single unanswered inbound
#: message is real but it is not yet a conversation, and a report that gave
#: every one of them a paragraph would bury the threads that matter.
SUBSTANTIVE_MESSAGE_FLOOR = 2


class ThreadShape(StrEnum):
    """The shape of the transcript, said without interpreting it."""

    #: Someone wrote in; nothing was sent back within the period.
    INBOUND_UNANSWERED = "inbound_unanswered"
    #: The owner wrote last. Whatever happens next is somebody else's move.
    AWAITING_REPLY = "awaiting_reply"
    #: Both sides wrote, and the last word was inbound.
    EXCHANGE_INBOUND_LAST = "exchange_inbound_last"
    #: The owner wrote and nobody had written in first.
    OUTBOUND_ONLY = "outbound_only"


@dataclass(frozen=True)
class Thread:
    """One conversation over one period, with everything a report may cite."""

    thread_id: str
    subject: str
    messages: list[Message] = field(default_factory=list)

    @property
    def message_ids(self) -> list[str]:
        """The evidence handles. Every generated claim must name some of these."""

        return [message.message_id for message in self.messages]

    @property
    def inbound(self) -> list[Message]:
        return [m for m in self.messages if m.direction is Direction.INBOUND]

    @property
    def outbound(self) -> list[Message]:
        return [m for m in self.messages if m.direction is Direction.OUTBOUND]

    @property
    def owner_replied(self) -> bool:
        return bool(self.outbound)

    @property
    def first_activity(self) -> datetime | None:
        moments = [m.occurred_at for m in self.messages if m.occurred_at]

        return min(moments) if moments else None

    @property
    def last_activity(self) -> datetime | None:
        moments = [m.occurred_at for m in self.messages if m.occurred_at]

        return max(moments) if moments else None

    @property
    def last_direction(self) -> Direction | None:
        dated = [m for m in self.messages if m.occurred_at]

        if not dated:
            return None

        return max(dated, key=lambda m: m.occurred_at).direction

    @property
    def shape(self) -> ThreadShape:
        if not self.inbound:
            return ThreadShape.OUTBOUND_ONLY

        if not self.outbound:
            return ThreadShape.INBOUND_UNANSWERED

        if self.last_direction is Direction.OUTBOUND:
            return ThreadShape.AWAITING_REPLY

        return ThreadShape.EXCHANGE_INBOUND_LAST

    @property
    def counterparties(self) -> list[Participant]:
        """Everyone on the thread who is not the mailbox owner, most-seen first.

        Ordered by how often they appear rather than alphabetically, so the
        person a conversation is actually with leads the list ahead of whoever
        was copied once.
        """

        tally: dict[str, tuple[Participant, int]] = {}

        for message in self.messages:
            people = (
                message.participants
                if message.direction is Direction.INBOUND
                else message.recipients
            )

            for person in people:
                if person.address == self._owner_address:
                    continue

                found, count = tally.get(person.address, (person, 0))
                # A later appearance carrying a name beats an earlier one
                # without: a display name is what the report prints.
                better = found if found.name else person
                tally[person.address] = (better, count + 1)

        return [
            person for person, _ in sorted(tally.values(), key=lambda item: -item[1])
        ]

    @property
    def organisations(self) -> list[str]:
        names = []

        for person in self.counterparties:
            name = organisation_of(person.domain)

            if name and name not in names:
                names.append(name)

        return names

    @property
    def has_attachments(self) -> bool:
        return any(message.has_attachments for message in self.messages)

    @property
    def substantive_count(self) -> int:
        return sum(
            1
            for message in self.messages
            if classify(message) is MessageClass.SUBSTANTIVE
        )

    @property
    def is_substantive(self) -> bool:
        """Whether this thread is worth a manager's attention.

        One outbound message clears the bar on its own: the owner writing to
        somebody is work, whatever came back. Otherwise a conversation needs to
        be a conversation — a lone automated notice is not one.
        """

        if self.outbound:
            return True

        return self.substantive_count >= SUBSTANTIVE_MESSAGE_FLOOR

    # Set by `reconstruct`, which is the only place that knows whose mailbox
    # this is. A property cannot ask, and passing the owner into every call
    # would put it in six signatures instead of one field.
    _owner_address: str = ""


def _subject_of(messages: list[Message]) -> str:
    """The thread's subject: the earliest one that is not a reply prefix.

    The first message names the conversation; later ones carry `Re:` chains
    and, in a long thread, drift as people edit the line.
    """

    dated = sorted(
        messages, key=lambda m: (m.occurred_at is None, m.occurred_at or datetime.min)
    )

    for message in dated:
        subject = message.subject.strip()
        lowered = subject.lower()

        if not lowered.startswith(("re:", "fw:", "fwd:")):
            return subject

    return dated[0].subject if dated else "(no subject)"


def reconstruct(correspondence: Correspondence) -> list[Thread]:
    """Group a period's messages into conversations, newest activity first.

    Ordering is by last activity rather than by volume: a thread that moved
    yesterday is more likely to need an answer than one that produced thirty
    messages a fortnight ago and stopped.
    """

    grouped: dict[str, list[Message]] = {}

    for message in correspondence.messages:
        grouped.setdefault(message.thread_id, []).append(message)

    threads = [
        Thread(
            thread_id=thread_id,
            subject=_subject_of(messages),
            messages=sorted(
                messages,
                key=lambda m: (m.occurred_at is None, m.occurred_at or datetime.min),
            ),
            _owner_address=correspondence.mailbox,
        )
        for thread_id, messages in grouped.items()
    ]

    return sorted(
        threads,
        key=lambda thread: (
            thread.last_activity is None,
            -(thread.last_activity.timestamp() if thread.last_activity else 0),
        ),
    )


@dataclass(frozen=True)
class ThreadInventory:
    """Every thread in a period, split into what is worth reading and what is not."""

    substantive: list[Thread]
    routine: list[Thread]

    @property
    def all(self) -> list[Thread]:
        return [*self.substantive, *self.routine]

    @property
    def automated_message_count(self) -> int:
        return sum(
            1
            for thread in self.routine
            for message in thread.messages
            if classify(message) is not MessageClass.SUBSTANTIVE
        )


def take_inventory(correspondence: Correspondence) -> ThreadInventory:
    """Reconstruct and sort the period's threads by whether they matter."""

    threads = reconstruct(correspondence)

    return ThreadInventory(
        substantive=[thread for thread in threads if thread.is_substantive],
        routine=[thread for thread in threads if not thread.is_substantive],
    )
