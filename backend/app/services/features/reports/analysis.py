"""What the conversations mean — the one stage that reads words.

Everything before this is arithmetic. This is where a model is asked the
question arithmetic cannot answer: what was this conversation *about*, what
moved, what is owed, and what does it mean for the business.

Three safeguards make that safe enough to print in a document somebody signs.

**Typed output.** The model returns JSON against a Pydantic schema, through the
existing `AnalysisEngine`. Malformed output raises rather than being coerced.

**Evidence validation.** Every claim names the message ids it rests on, and
`_kept` discards any claim whose ids are not in the period being analysed. A
model that invents a plausible thread cannot get it into the report, because
the invention has no id — this is the mechanism, not the instruction, and it is
what distinguishes this design from "we asked it nicely not to make things up".

**A deterministic floor.** If the model is not configured, times out, or
returns something unusable, `analyse` returns an analysis derived from the
thread structure alone rather than raising. That report is thinner — it has no
narrative — but it is a real report built from real threads, which is still
considerably more than the count-based summary it replaces. The endpoint never
breaks because a provider is down.

The model never sees a message id it could not also have been given: ids come
from the `Correspondence`, are passed in, and are checked on the way back.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.exceptions import AnalysisValidationError, LLMServiceError
from app.services.engines.analysis.analysis_engine import analysis_engine
from app.services.features.reports.period import Period
from app.services.features.reports.threads import Thread, ThreadInventory, ThreadShape

logger = logging.getLogger(__name__)

#: Threads per analysis call. A month of a busy mailbox is a few hundred
#: substantive threads; sending them all at once produces one summary of
#: everything, which is the failure this pipeline exists to cure. Batching
#: keeps each call's attention on a readable number of conversations.
#:
#: Four rather than twelve: measured on a live month (183 substantive threads),
#: a twelve-thread batch took long enough that only two calls finished inside
#: the 90s analysis budget. Smaller requests let more batches complete in the
#: same budget, and each one that does not finish costs four threads, not twelve.
THREADS_PER_BATCH = 4

#: How many threads any one period's analysis reads. Ordered by significance,
#: so the cap drops the least active conversations first and says so.
MAX_THREADS_ANALYSED = 120

#: How much of an exception's own message is logged. Enough to identify a
#: provider error or a schema complaint; short enough that a model echoing part
#: of a mail body into an error string cannot spill it into the log.
ERROR_DETAIL_CHARS = 300


class Budget:
    """How much wall-clock the model calls behind one report may spend.

    A deadline rather than a per-call timeout, because per-call timeouts are
    what produced the original problem: each call was individually reasonable
    and eighteen of them in a row were not. When the budget is spent the
    remaining conversations are described structurally and the report says so,
    which is the same degradation the pipeline already performs when no model
    is configured at all.
    """

    def __init__(self, seconds: float | None) -> None:
        self.seconds = seconds
        self._started = time.monotonic()
        self.exhausted = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def spent(self) -> bool:
        if self.seconds is None:
            return False

        if self.elapsed >= self.seconds:
            self.exhausted = True

        return self.exhausted


#: How much of a message body the model is shown. Enough for a request and its
#: answer; short enough that a batch of twelve threads stays readable.
BODY_EXCERPT = 900


class EvidenceStatus(StrEnum):
    """How far along something is, said at the strength the evidence supports."""

    EXPLICITLY_COMPLETED = "explicitly_completed"
    SUBSTANTIALLY_PROGRESSED = "substantially_progressed"
    RESPONDED_BUT_PENDING = "responded_but_pending"
    AWAITING_EXTERNAL_RESPONSE = "awaiting_external_response"
    OPEN_UNRESOLVED = "open_unresolved"
    POSSIBLE_FOLLOW_UP = "possible_follow_up"
    NOT_DETERMINABLE = "not_determinable"


class ThreadFinding(BaseModel):
    """One analysed conversation."""

    thread_id: str
    topic: str = ""
    category: str = "other"
    summary: str = ""
    owner_actions: list[str] = Field(default_factory=list)
    other_actions: list[str] = Field(default_factory=list)
    outcome: str | None = None
    status: EvidenceStatus = EvidenceStatus.NOT_DETERMINABLE
    pending_dependency: str | None = None
    deadline: str | None = None
    significance: str = ""
    risk: str | None = None
    evidence: list[str] = Field(default_factory=list)


class ThreadFindings(BaseModel):
    threads: list[ThreadFinding] = Field(default_factory=list)


class Workstream(BaseModel):
    name: str
    purpose: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    activity: str = ""
    owner_contribution: str = ""
    status: str = ""
    next_step: str | None = None
    risks: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    decision: str
    evidence: list[str] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    question: str
    evidence: list[str] = Field(default_factory=list)


class Risk(BaseModel):
    issue: str
    impact: str = ""
    status: str = ""
    evidence: list[str] = Field(default_factory=list)


class FollowUp(BaseModel):
    item: str
    responsible: str = ""
    deadline: str | None = None
    next_action: str = ""
    evidence: list[str] = Field(default_factory=list)


class PeriodSynthesis(BaseModel):
    workstreams: list[Workstream] = Field(default_factory=list)
    executive_summary: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    unresolved: list[OpenQuestion] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    follow_ups: list[FollowUp] = Field(default_factory=list)
    workload: str = ""
    management_summary: list[str] = Field(default_factory=list)


class CarriedItem(BaseModel):
    item: str
    from_week: str = ""
    status: str = ""
    evidence: list[str] = Field(default_factory=list)


class CrossPeriodSynthesis(BaseModel):
    executive_summary: list[str] = Field(default_factory=list)
    carried_forward: list[CarriedItem] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    management_summary: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PeriodAnalysis:
    """One period, analysed. The structure a report is composed from."""

    period: Period
    findings: list[ThreadFinding] = field(default_factory=list)
    synthesis: PeriodSynthesis = field(default_factory=PeriodSynthesis)
    #: True when no model contributed. The report says so in its header rather
    #: than presenting a structural summary as though it were an analysis.
    generated: bool = False
    limitations: list[str] = field(default_factory=list)


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())

    return collapsed[:BODY_EXCERPT]


def render_threads(threads: list[Thread]) -> str:
    """Threads as the model sees them: transcripts with citable ids."""

    blocks: list[str] = []

    for thread in threads:
        lines = [
            f"THREAD {thread.thread_id}",
            f"subject: {thread.subject}",
            f"shape: {thread.shape.value}; messages: {len(thread.messages)}; "
            f"owner replied: {'yes' if thread.owner_replied else 'no'}",
        ]

        if thread.organisations:
            lines.append(f"organisations: {', '.join(thread.organisations[:4])}")

        for message in thread.messages:
            when = (
                f"{message.occurred_at:%Y-%m-%d %H:%M}"
                if message.occurred_at
                else "undated"
            )
            who = message.sender.display if message.sender else "unknown"
            lines.append(
                f"  [{message.message_id}] {when} {message.direction.value} "
                f"from {who}: {_excerpt(message.body) or '(no body captured)'}"
            )

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _rank(thread: Thread) -> tuple:
    """Most report-worthy first: conversations the owner joined, then activity."""

    return (
        not thread.owner_replied,
        -len(thread.messages),
        -(thread.last_activity.timestamp() if thread.last_activity else 0),
    )


def _kept(evidence: list[str], known: set[str]) -> list[str]:
    """Only the ids that exist in this period. The fabrication gate."""

    return [item for item in evidence if item in known]


def _supported(evidence: list[str], known: set[str]) -> bool:
    return bool(_kept(evidence, known))


def _prune_synthesis(synthesis: PeriodSynthesis, known: set[str]) -> PeriodSynthesis:
    """Drop every claim whose evidence is not in this period.

    Executive-summary bullets, the workload paragraph and the management
    observations carry no ids of their own — they are commentary over the
    workstreams below them, which are themselves gated. Keeping them while
    dropping an unsupported workstream is the right trade: the alternative
    demands a citation on a sentence that legitimately summarises ten.
    """

    return PeriodSynthesis(
        workstreams=[
            item.model_copy(update={"evidence": _kept(item.evidence, known)})
            for item in synthesis.workstreams
            if _supported(item.evidence, known)
        ],
        executive_summary=synthesis.executive_summary,
        decisions=[
            item.model_copy(update={"evidence": _kept(item.evidence, known)})
            for item in synthesis.decisions
            if _supported(item.evidence, known)
        ],
        unresolved=[
            item.model_copy(update={"evidence": _kept(item.evidence, known)})
            for item in synthesis.unresolved
            if _supported(item.evidence, known)
        ],
        risks=[
            item.model_copy(update={"evidence": _kept(item.evidence, known)})
            for item in synthesis.risks
            if _supported(item.evidence, known)
        ],
        follow_ups=[
            item.model_copy(update={"evidence": _kept(item.evidence, known)})
            for item in synthesis.follow_ups
            if _supported(item.evidence, known)
        ],
        workload=synthesis.workload,
        management_summary=synthesis.management_summary,
    )


_STATUS_FROM_SHAPE = {
    ThreadShape.INBOUND_UNANSWERED: EvidenceStatus.OPEN_UNRESOLVED,
    ThreadShape.AWAITING_REPLY: EvidenceStatus.AWAITING_EXTERNAL_RESPONSE,
    ThreadShape.EXCHANGE_INBOUND_LAST: EvidenceStatus.RESPONDED_BUT_PENDING,
    ThreadShape.OUTBOUND_ONLY: EvidenceStatus.AWAITING_EXTERNAL_RESPONSE,
}

_SHAPE_PROSE = {
    ThreadShape.INBOUND_UNANSWERED: (
        "Correspondence was received and no reply was sent within this period."
    ),
    ThreadShape.AWAITING_REPLY: (
        "The mailbox owner wrote last; no further reply arrived in this period."
    ),
    ThreadShape.EXCHANGE_INBOUND_LAST: (
        "Both sides corresponded and the most recent message was inbound."
    ),
    ThreadShape.OUTBOUND_ONLY: (
        "The mailbox owner initiated this; nothing came back within the period."
    ),
}


def _structural_summary(thread: Thread) -> str:
    who = ", ".join(p.display for p in thread.counterparties[:3])

    return (
        f"{len(thread.messages)} message(s) with {who or 'no external party'}. "
        f"{_SHAPE_PROSE[thread.shape]}"
    )


def structural_findings(threads: list[Thread]) -> list[ThreadFinding]:
    """Findings derived from thread shape alone, with no model.

    The deterministic floor. Every field here is recomputable from the rows,
    which is why nothing in it needs a citation check — and why it is honest to
    serve when the model is unavailable.
    """

    return [
        ThreadFinding(
            thread_id=thread.thread_id,
            topic=thread.subject,
            category="other",
            summary=_structural_summary(thread),
            status=_STATUS_FROM_SHAPE[thread.shape],
            significance="",
            evidence=thread.message_ids,
        )
        for thread in threads
    ]


def _detail(exc: BaseException) -> str:
    """One line naming a failure, with no correspondence in it.

    The exception's own message only. A batch failure used to log the type in a
    `extra` dict that the default formatter does not print, so the line read
    `report_thread_batch_failed` and nothing else — true, and useless.
    """

    return f"{type(exc).__name__}: {exc}"[:ERROR_DETAIL_CHARS]


def _analyse_threads(
    threads: list[Thread], *, owner: str, budget: Budget | None = None
) -> list[ThreadFinding]:
    known = {message_id for thread in threads for message_id in thread.message_ids}
    findings: list[ThreadFinding] = []
    batches = (len(threads) + THREADS_PER_BATCH - 1) // THREADS_PER_BATCH

    for index, start in enumerate(range(0, len(threads), THREADS_PER_BATCH), start=1):
        batch = threads[start : start + THREADS_PER_BATCH]

        if budget is not None and budget.spent():
            # Not an error, and not a retry: the remaining batches are served
            # from their structure so the request ends on time. `analyse` turns
            # this into a limitation the reader can see.
            logger.info(
                "report_thread_budget_spent batch=%d/%d remaining_threads=%d "
                "elapsed=%.1fs budget=%.1fs",
                index,
                batches,
                len(threads) - start,
                budget.elapsed,
                budget.seconds or 0.0,
            )
            findings.extend(structural_findings(threads[start:]))
            break

        started = time.monotonic()

        try:
            result = analysis_engine.run(
                "report_thread_analysis",
                ThreadFindings,
                # Constrained by the provider to the schema, rather than trusted
                # to be valid JSON because the prompt asked: long batches came
                # back with unescaped backslashes and missing commas.
                structured_output=True,
                owner=owner,
                threads=render_threads(batch),
            )
        except (AnalysisValidationError, LLMServiceError, Exception) as exc:  # noqa: BLE001
            # One bad batch must not lose the rest of the month. The threads it
            # covered fall back to their structural findings, which is a worse
            # description of them but not an absent one.
            #
            # `exc_info` carries the traceback: the identity of the failing
            # call is the whole diagnostic value of this line, and without it a
            # provider outage, a malformed response and a bug in this module
            # are indistinguishable in a log.
            logger.warning(
                "report_thread_batch_failed batch=%d/%d threads=%d "
                "elapsed=%.1fs error=%s",
                index,
                batches,
                len(batch),
                time.monotonic() - started,
                _detail(exc),
                exc_info=True,
            )
            findings.extend(structural_findings(batch))
            continue

        logger.debug(
            "report_thread_batch_ok batch=%d/%d threads=%d elapsed=%.1fs",
            index,
            batches,
            len(batch),
            time.monotonic() - started,
        )

        by_id = {thread.thread_id: thread for thread in batch}
        analysed: set[str] = set()

        for finding in result.threads:
            if finding.thread_id not in by_id:
                # A thread the model invented or misnamed. There is nothing to
                # attach it to and nothing to verify it against.
                continue

            kept = _kept(finding.evidence, known)

            if not kept:
                continue

            findings.append(finding.model_copy(update={"evidence": kept}))
            analysed.add(finding.thread_id)

        # Threads the model skipped still belong in the report.
        findings.extend(
            structural_findings([t for t in batch if t.thread_id not in analysed])
        )

    return findings


def _tasks_and_calendar(context: str | None) -> str:
    return context or (
        "No task or calendar evidence was supplied for this period. Report on "
        "the correspondence alone and do not treat the absence of tasks as "
        "evidence that little work was done."
    )


def analyse(
    inventory: ThreadInventory,
    *,
    period: Period,
    owner: str,
    context: str | None = None,
    use_model: bool = True,
    findings: list[ThreadFinding] | None = None,
    budget: Budget | None = None,
) -> PeriodAnalysis:
    """Analyse one period's threads into the structure a report is built from.

    Never raises for a model failure. A caller gets either a generated analysis
    or a structural one, and `PeriodAnalysis.generated` says which.

    `findings` supplies per-conversation analysis that has already been done,
    which is how a month reuses one pass over its threads for every week inside
    it instead of analysing each conversation twice. `budget` bounds what the
    model calls may spend in total.
    """

    ranked = sorted(inventory.substantive, key=_rank)
    limitations: list[str] = []

    if len(ranked) > MAX_THREADS_ANALYSED:
        limitations.append(
            f"{len(ranked)} substantive conversations were identified and the "
            f"{MAX_THREADS_ANALYSED} most active were analysed in detail. The "
            "remainder are counted but not described individually."
        )
        ranked = ranked[:MAX_THREADS_ANALYSED]

    if not ranked:
        return PeriodAnalysis(period=period, limitations=limitations)

    if not use_model:
        return PeriodAnalysis(
            period=period,
            findings=structural_findings(ranked),
            limitations=[
                *limitations,
                "Narrative analysis was not available for this report, so "
                "conversations are described by their structure — who wrote, "
                "when, and which side replied last — rather than by their "
                "content.",
            ],
        )

    wanted = {thread.thread_id for thread in ranked}
    reused = (
        [finding for finding in findings if finding.thread_id in wanted]
        if findings is not None
        else None
    )
    # Reuse only covers what it covers: a thread with no finding in the
    # supplied set still needs one, and taking it structurally is cheaper and
    # more honest than a second model call for a conversation the caller has
    # already paid to analyse.
    if reused is not None:
        covered = {finding.thread_id for finding in reused}
        analysed = reused + structural_findings(
            [thread for thread in ranked if thread.thread_id not in covered]
        )
    else:
        analysed = _analyse_threads(ranked, owner=owner, budget=budget)

    findings = analysed
    known = {message_id for thread in ranked for message_id in thread.message_ids}

    if budget is not None and budget.exhausted:
        limitations.append(
            "The time allowed for narrative analysis was reached before every "
            "conversation had been read, so the remainder are described by "
            "their structure rather than their content."
        )

    if budget is not None and budget.spent():
        # The synthesis is one more call, and the point of the budget is that
        # the request ends. The findings above are still a report.
        return PeriodAnalysis(
            period=period,
            findings=findings,
            generated=any(
                finding.summary and finding.significance for finding in findings
            ),
            limitations=[
                *limitations,
                "The period-level synthesis was not produced, because the time "
                "allowed for narrative analysis was already spent.",
            ],
        )

    try:
        synthesis = analysis_engine.run(
            "report_period_synthesis",
            PeriodSynthesis,
            owner=owner,
            period=f"{period.label}"
            + (" (partial — the period has not finished)" if period.is_partial else ""),
            threads=json.dumps(
                [finding.model_dump(mode="json") for finding in findings], indent=1
            ),
            context=_tasks_and_calendar(context),
        )
    except Exception as exc:  # noqa: BLE001 - a report is better than an error
        logger.warning(
            "report_synthesis_failed period=%s threads=%d error=%s",
            period.key,
            len(findings),
            _detail(exc),
            exc_info=True,
        )

        return PeriodAnalysis(
            period=period,
            findings=findings,
            generated=any(
                finding.summary and finding.significance for finding in findings
            ),
            limitations=[
                *limitations,
                "Per-conversation analysis succeeded but the period-level "
                "synthesis did not, so workstreams and management observations "
                "are absent from this report.",
            ],
        )

    return PeriodAnalysis(
        period=period,
        findings=findings,
        synthesis=_prune_synthesis(synthesis, known),
        generated=True,
        limitations=limitations,
    )


def synthesise_across(
    analyses: list[PeriodAnalysis],
    *,
    period: Period,
    owner: str,
    use_model: bool = True,
    budget: Budget | None = None,
) -> CrossPeriodSynthesis:
    """The month-level reading of several weekly analyses.

    Returns an empty synthesis rather than raising: the weekly sections are
    already written by this point, and a missing cross-week paragraph is a
    smaller loss than a failed report.
    """

    if not use_model or len(analyses) < 2:
        return CrossPeriodSynthesis()

    if budget is not None and budget.spent():
        return CrossPeriodSynthesis()

    payload = [
        {
            "week": item.period.label,
            "workstreams": [
                w.model_dump(mode="json") for w in item.synthesis.workstreams
            ],
            "follow_ups": [
                f.model_dump(mode="json") for f in item.synthesis.follow_ups
            ],
            "risks": [r.model_dump(mode="json") for r in item.synthesis.risks],
        }
        for item in analyses
    ]

    try:
        return analysis_engine.run(
            "report_cross_period",
            CrossPeriodSynthesis,
            owner=owner,
            period=f"{period.label}"
            + (" (partial — the period has not finished)" if period.is_partial else ""),
            weeks=json.dumps(payload, indent=1),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "report_cross_period_failed period=%s weeks=%d error=%s",
            period.key,
            len(analyses),
            _detail(exc),
            exc_info=True,
        )

        return CrossPeriodSynthesis()


def deadline_date(value: str | None) -> datetime | None:
    """Parse a model-supplied deadline, or None. Never guesses one."""

    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
