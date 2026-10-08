"""Judging a report against the evidence it was written from.

A fixed word count is the wrong test in both directions: it fails a genuinely
quiet week and passes a padded one. So every threshold here is a function of
how much substantive material the period actually held. Ten conversations
should produce a short report and that is a pass; a hundred and forty producing
five bullets is the failure this whole pipeline exists to prevent, and it is
what `Verdict.failed` is for.

The checks are deliberately structural rather than linguistic. "Does this read
well" is not something a validator can answer, but "does this document contain
workstreams, a completed-versus-pending distinction, and named conversations,
in proportion to the evidence" is — and those are the properties whose absence
made the old report useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.features.reports.composition import ReportDocument
from app.services.features.reports.threads import ThreadInventory

#: Below this many substantive threads a period is genuinely quiet, and a short
#: report is the honest answer rather than a defect to be expanded away.
QUIET_PERIOD_THREADS = 5

#: Words expected per substantive thread, used to scale the floor. Deliberately
#: modest: the floor is a smoke alarm for a collapsed report, not a target.
WORDS_PER_THREAD = 22

#: No report is ever required to be longer than this, however much evidence
#: exists. Past it, more words stop being more information.
MAX_EXPECTED_WORDS = 3_000

REQUIRED_SECTIONS = (
    "Executive Summary",
    "Major Workstreams / Business Activities",
    "Important Conversations / Threads",
    "Actions and Outcomes",
    "Pending / Follow-up Items",
    "Issues, Risks, and Blockers",
    "Period-End Status",
    "Management-Level Summary",
)


@dataclass(frozen=True)
class Verdict:
    """Whether a report is good enough to serve, and what is wrong with it."""

    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected_words: int = 0

    @property
    def failed(self) -> bool:
        return not self.ok


def expected_words(inventory: ThreadInventory) -> int:
    """How long a report over this much evidence ought to be, at minimum."""

    substantive = len(inventory.substantive)

    if substantive <= QUIET_PERIOD_THREADS:
        # Enough for the structure and an honest explanation of the quiet.
        return 220

    return min(MAX_EXPECTED_WORDS, 400 + substantive * WORDS_PER_THREAD)


def _mostly_counts(markdown: str) -> bool:
    """Whether the document is more arithmetic than description.

    The old report was almost entirely digits and the words around them. A
    genuine report mentions numbers; it is not made of them.
    """

    words = markdown.split()

    if not words:
        return True

    numeric = sum(1 for word in words if any(ch.isdigit() for ch in word))

    return numeric > len(words) * 0.25


def assess(
    document: ReportDocument,
    inventory: ThreadInventory,
    *,
    narrative: bool = True,
) -> Verdict:
    """Check a rendered report against the evidence volume behind it.

    `narrative` says whether a model contributed. Workstreams and an executive
    summary are derived from language, so a deterministic report cannot have
    them — demanding them anyway would mark every fallback report a failure and
    train whoever reads the verdict to ignore it. The length and section checks
    still apply, because those are exactly what a collapsed report loses.
    """

    failures: list[str] = []
    warnings: list[str] = []
    target = expected_words(inventory)
    substantive = len(inventory.substantive)

    missing = [
        title for title in REQUIRED_SECTIONS if title not in document.section_titles
    ]

    if missing:
        failures.append(f"Missing required section(s): {', '.join(missing)}.")

    if document.word_count < target:
        failures.append(
            f"The report is {document.word_count} words against {substantive} "
            f"substantive conversation(s); at least {target} were expected."
        )

    if (
        narrative
        and substantive > QUIET_PERIOD_THREADS
        and document.workstream_count == 0
    ):
        failures.append(
            f"No workstreams were identified from {substantive} substantive "
            "conversation(s)."
        )

    if substantive > QUIET_PERIOD_THREADS and document.thread_count == 0:
        failures.append("No conversations were described individually.")

    if _mostly_counts(document.markdown):
        failures.append(
            "The report is predominantly numeric — it reads as statistics "
            "rather than as an account of the period's work."
        )

    if (
        narrative
        and substantive > QUIET_PERIOD_THREADS
        and document.workstream_count < 3
    ):
        warnings.append(
            f"Only {document.workstream_count} workstream(s) were identified "
            f"from {substantive} substantive conversation(s)."
        )

    if not narrative:
        warnings.append(
            "No narrative analysis contributed to this report, so workstreams "
            "and an executive summary are absent by construction rather than "
            "by omission."
        )

    if substantive <= QUIET_PERIOD_THREADS:
        warnings.append(
            f"This period held {substantive} substantive conversation(s), so a "
            "short report is expected rather than a sign of a problem."
        )

    return Verdict(
        ok=not failures,
        failures=failures,
        warnings=warnings,
        expected_words=target,
    )
