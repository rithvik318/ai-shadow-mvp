"""The report pipeline, end to end.

One entry point, `generate`, which walks the stages in order:

    correspondence -> classification -> threads -> analysis -> composition
                                                       |
                                              quality -+ (one retry)

Everything it needs is passed in. The pipeline itself opens no connection and
reads no configuration, which is what lets the whole thing be exercised against
the collected JSON with no tenant, no model and no database.

**One pass over the conversations, read several ways.** The weekly sections
need each week's *synthesis*, not a second analysis of the same conversations:
a thread in the first week of August is the same thread whether it is being
read as part of that week or of the month. So the per-conversation analysis
runs once over the whole period and each week is given the slice of it that
belongs to that week. Before this, a month analysed every conversation twice —
eighteen model calls where twelve will do, measured on the August corpus.

**Weekly segmentation is the pipeline's own idea, not the caller's.** A period
longer than a week is cut into week-long segments, each analysed separately,
and then read across. That is what makes "September so far" resolve into 1–7,
8–13 and a synthesis over both rather than one flat summary of thirteen days —
and because the cut comes from `split_into_weeks`, it works for any month and
any range without a calendar special case anywhere.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from app.services.features.reports import analysis as analysis_stage
from app.services.features.reports import composition, quality
from app.services.features.reports.analysis import Budget, PeriodAnalysis
from app.services.features.reports.correspondence import Correspondence
from app.services.features.reports.period import Period, split_into_weeks
from app.services.features.reports.threads import ThreadInventory, take_inventory

logger = logging.getLogger(__name__)

#: A period longer than this is reported week by week plus a synthesis. Exactly
#: a week is one segment: splitting it would print "week 1" above a report that
#: already covers that week.
WEEKLY_SEGMENT_THRESHOLD = timedelta(days=7)


@dataclass(frozen=True)
class ReportResult:
    """A generated report and everything needed to judge or debug it."""

    document: composition.ReportDocument
    verdict: quality.Verdict
    inventory: ThreadInventory
    analysis: PeriodAnalysis
    weekly: list[tuple[Period, PeriodAnalysis]] = field(default_factory=list)

    @property
    def markdown(self) -> str:
        return self.document.markdown


def _slice(correspondence: Correspondence, period: Period) -> Correspondence:
    """The same correspondence narrowed to a sub-window."""

    from dataclasses import replace

    return replace(
        correspondence,
        period=period,
        messages=[
            message
            for message in correspondence.messages
            if period.contains(message.occurred_at)
        ],
    )


def generate(
    correspondence: Correspondence,
    *,
    period: Period | None = None,
    owner: str | None = None,
    context: str | None = None,
    use_model: bool = True,
    budget_seconds: float | None = None,
) -> ReportResult:
    """Produce a report for one mailbox over one period.

    `use_model=False` runs the whole pipeline deterministically, which is what
    the tests do and what a deployment with no LLM configured falls back to.
    The document is thinner but structurally identical, and its header says
    which it is.

    `budget_seconds` bounds every model call this report makes, together. It is
    what stops a slow provider from turning a request into an open-ended wait:
    when the budget is gone the remaining conversations are described
    structurally and the report says so. `None` means no bound, which is only
    appropriate for an offline run.
    """

    started = time.monotonic()
    window = period or correspondence.period
    scoped = _slice(correspondence, window) if period is not None else correspondence
    inventory = take_inventory(scoped)
    who = owner or correspondence.owner_name or correspondence.mailbox
    budget = Budget(budget_seconds) if use_model else None

    logger.info(
        "report_pipeline_start period=%s messages=%d substantive=%d routine=%d "
        "use_model=%s budget=%s",
        window.key,
        len(scoped.messages),
        len(inventory.substantive),
        len(inventory.routine),
        use_model,
        f"{budget_seconds:.0f}s" if budget_seconds else "none",
    )

    segments = (
        split_into_weeks(window)
        if window.end - window.start > WEEKLY_SEGMENT_THRESHOLD
        else [window]
    )

    # The whole period first, because its per-conversation findings are what
    # every week below is then read from.
    whole = analysis_stage.analyse(
        inventory,
        period=window,
        owner=who,
        context=context,
        use_model=use_model,
        budget=budget,
    )

    weekly: list[tuple[Period, PeriodAnalysis]] = []

    if len(segments) > 1:
        for segment in segments:
            part = _slice(scoped, segment)
            weekly.append(
                (
                    segment,
                    analysis_stage.analyse(
                        take_inventory(part),
                        period=segment,
                        owner=who,
                        context=context,
                        use_model=use_model,
                        findings=whole.findings,
                        budget=budget,
                    ),
                )
            )

    cross = analysis_stage.synthesise_across(
        [item for _, item in weekly],
        period=window,
        owner=who,
        use_model=use_model,
        budget=budget,
    )

    document = composition.compose(
        whole,
        owner=who,
        mailbox=correspondence.mailbox,
        inventory=inventory,
        message_count=len(scoped.messages),
        limitations=scoped.limitations,
        weekly=weekly,
        cross=cross,
    )

    verdict = quality.assess(document, inventory, narrative=whole.generated)

    if verdict.failed:
        # The retry is a *composition* retry, not a second model call: the
        # structured analysis is already in hand, and if it was thin then
        # asking the same question again would produce the same answer. What
        # the document can still do is print everything the analysis holds
        # rather than the top of each list — which is the difference between a
        # report that looks empty and one that is.
        logger.info(
            "report_quality_expansion",
            extra={"failures": verdict.failures, "words": document.word_count},
        )
        document = composition.compose(
            whole,
            owner=who,
            mailbox=correspondence.mailbox,
            inventory=inventory,
            message_count=len(scoped.messages),
            limitations=[
                *scoped.limitations,
                *(
                    [
                        "This report did not meet the expected level of detail "
                        "on its first pass: " + " ".join(verdict.failures)
                    ]
                    if verdict.failures
                    else []
                ),
            ],
            weekly=weekly,
            cross=cross,
            verbose=True,
        )
        verdict = quality.assess(document, inventory, narrative=whole.generated)

    logger.info(
        "report_pipeline_done period=%s messages=%d words=%d narrative=%s "
        "weeks=%d quality_ok=%s elapsed=%.1fs",
        window.key,
        len(scoped.messages),
        document.word_count,
        whole.generated,
        len(weekly),
        verdict.ok,
        time.monotonic() - started,
    )

    return ReportResult(
        document=document,
        verdict=verdict,
        inventory=inventory,
        analysis=whole,
        weekly=weekly,
    )
