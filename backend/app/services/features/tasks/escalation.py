"""When a task stops being one person's problem, and who to tell.

Pure: no database, no settings lookup, no clock of its own. Every input is
passed in, which is what lets the boundaries be tested exactly and what stops
two callers — the task endpoint and the weekly report — reaching different
conclusions about the same task.

Two rules are the whole point of this module.

**Escalation is derived, never stored.** Like overdue, it is a function of the
task and the moment. A stored flag would be right when it was written and
wrong an hour later, and keeping it honest would need a background job whose
only purpose is to correct a value that could simply have been computed.
`Task.escalation_requested` is a different thing and *is* stored: it is a
person saying "this needs escalating", which is evidence rather than a
conclusion.

**A target is identified or it is not.** The suggested target comes from
configuration, and from nowhere else. The alternative — picking somebody off
the thread, a cc line, or a domain — is a guess, and the cost of guessing
wrong is an escalation email to the wrong person about somebody else's late
work. When no target is configured this says so in words, and the report shows
those words rather than an empty field that reads like an oversight.

The counterparty on the task (`contact_*`) is deliberately *not* a fallback
target. They are usually the person who has not replied — escalating to them
is just another follow-up, dressed up.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.services.features.tasks.urgency import (
    RESOLVED_STATUSES,
    TaskPriority,
    TaskStatus,
)

#: Shown wherever a target would be, when none is configured. A sentence, not
#: an empty string: the report has to read as "nobody is set up for this",
#: which is actionable, rather than as a rendering bug.
NO_TARGET_IDENTIFIED = "Escalation target not identified"

# Why a task is being escalated, in the order they are checked. First match
# wins, so the most specific reason is the one shown. A task can satisfy
# several; showing all of them turns one problem into a list of restatements.
REASON_REQUESTED = "Escalation was explicitly requested"
REASON_BLOCKED = "Blocked and not progressing"
REASON_OVERDUE_HIGH = "High-priority task is overdue"
REASON_LONG_OVERDUE = "Overdue beyond the escalation threshold"
REASON_UNANSWERED = "Important request with no response"


class TargetSource(StrEnum):
    """Where a suggested escalation target came from.

    Recorded so a reader can tell a configured address from an absent one
    without comparing strings, and so that a future source — an org chart, a
    manager field on the user — is an added member rather than a change of
    meaning for the existing ones.
    """

    CONFIGURED = "configured"
    NOT_IDENTIFIED = "not_identified"


@dataclass(frozen=True)
class EscalationVerdict:
    """Whether this task needs somebody else, and everything to say about it."""

    required: bool
    reason: str | None = None
    recommended_action: str | None = None

    target_name: str | None = None
    target_address: str | None = None
    target_source: TargetSource = TargetSource.NOT_IDENTIFIED

    #: The counterparty the task concerns — who has not replied — as distinct
    #: from who to escalate *to*. Carried so the report can offer "draft a
    #: follow-up to them" alongside "escalate".
    contact_name: str | None = None
    contact_address: str | None = None

    @property
    def target_display(self) -> str:
        """Who to escalate to, or a plain statement that nobody is identified."""

        if not self.target_address:
            return NO_TARGET_IDENTIFIED

        if self.target_name:
            return f"{self.target_name} <{self.target_address}>"

        return self.target_address


def _reason(
    *,
    status: TaskStatus,
    priority: TaskPriority,
    is_overdue: bool,
    overdue_seconds: float | None,
    escalation_requested: bool,
    source: str,
    escalation_seconds: float,
) -> str | None:
    """Why this task needs a person's attention, or None if it does not."""

    if escalation_requested:
        return REASON_REQUESTED

    if status is TaskStatus.BLOCKED:
        return REASON_BLOCKED

    if not is_overdue:
        # Nothing below this line applies to a task that is merely approaching
        # its deadline. Escalating those would make escalation meaningless.
        return None

    if priority in (TaskPriority.HIGH, TaskPriority.URGENT):
        return REASON_OVERDUE_HIGH

    if (overdue_seconds or 0.0) >= escalation_seconds:
        return REASON_LONG_OVERDUE

    if source != "manual":
        # Overdue, derived from a message somebody sent, and nothing has moved.
        # The evidence for "important request with no response" is exactly
        # that: a task created from an inbound message passed its date
        # untouched.
        return REASON_UNANSWERED

    return None


def _action(reason: str, *, contact_address: str | None, has_target: bool) -> str:
    """What a person should do next. Never a promise of automation.

    Every one of these is something a human then does. Nothing in this system
    sends an escalation on its own, and the wording is chosen so the report
    never implies otherwise.
    """

    if reason == REASON_BLOCKED:
        return "Review what this is blocked on and decide who can unblock it."

    if has_target:
        return "Prepare an escalation email for review."

    if contact_address:
        return (
            f"Draft a follow-up to {contact_address} for review, or mark the "
            "task complete if it has already been resolved."
        )

    return "Review this item and decide whether to chase it or close it."


def evaluate(
    *,
    status: TaskStatus,
    priority: TaskPriority,
    is_overdue: bool,
    overdue_seconds: float | None,
    escalation_requested: bool,
    source: str,
    escalation_seconds: float,
    contact_name: str | None = None,
    contact_address: str | None = None,
    configured_target_name: str | None = None,
    configured_target_address: str | None = None,
) -> EscalationVerdict:
    """Decide whether one task needs escalating, and to whom.

    `escalation_seconds` is passed rather than read from settings so this stays
    pure and so a test can sit exactly on the boundary. Production passes
    `TASK_ESCALATION_DAYS * 86400`.
    """

    if status in RESOLVED_STATUSES:
        # Finished work is never escalated, however late it once was, and a
        # cancelled task is not an outstanding request.
        return EscalationVerdict(required=False)

    reason = _reason(
        status=status,
        priority=priority,
        is_overdue=is_overdue,
        overdue_seconds=overdue_seconds,
        escalation_requested=escalation_requested,
        source=source,
        escalation_seconds=escalation_seconds,
    )

    if reason is None:
        return EscalationVerdict(
            required=False, contact_name=contact_name, contact_address=contact_address
        )

    target_address = (configured_target_address or "").strip() or None
    target_source = (
        TargetSource.CONFIGURED if target_address else TargetSource.NOT_IDENTIFIED
    )

    return EscalationVerdict(
        required=True,
        reason=reason,
        recommended_action=_action(
            reason, contact_address=contact_address, has_target=bool(target_address)
        ),
        target_name=(configured_target_name or "").strip() or None
        if target_address
        else None,
        target_address=target_address,
        target_source=target_source,
        contact_name=contact_name,
        contact_address=contact_address,
    )
