"""The document, rendered from the analysis rather than from the mailbox.

This module never touches a message, a count or a model. It takes the
structured `PeriodAnalysis` and turns it into the thirteen sections a manager
reads. Keeping it pure is what lets the whole report be asserted in a test: the
same analysis always produces the same document.

Two rules shape the output.

**A section that has nothing to say says nothing, and says why.** The old
report dropped empty sections entirely, which is how a thin dataset produced a
five-line document — the reader could not tell a quiet month from a broken
pipeline. Here the heading stays and carries an explicit note, because "no
deadlines were stated in the correspondence" is a finding and a missing heading
is not.

**Partial periods are labelled everywhere they could mislead**, not only in the
header. A month-to-date report is a different claim from a monthly one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.features.reports.analysis import (
    CrossPeriodSynthesis,
    EvidenceStatus,
    PeriodAnalysis,
)
from app.services.features.reports.period import Period, PeriodKind, month_of
from app.services.features.reports.threads import ThreadInventory

#: Conversations described individually in section 3. Beyond this the section
#: stops being a reading list and becomes a data export.
IMPORTANT_THREAD_LIMIT = 10

#: The same limits when the quality gate has asked for a second, fuller pass.
#: A period with a hundred substantive conversations genuinely needs more of
#: them named; capping at ten there is what makes a busy month read like a
#: quiet one.
VERBOSE_THREAD_LIMIT = 30
ACTIONS_PER_STATUS = 8
VERBOSE_ACTIONS_PER_STATUS = 25

_UNKNOWN = "Not determinable from the available data."
_NO_NEXT_STEP = "No next step is stated in the correspondence."

_STATUS_HEADINGS = (
    (EvidenceStatus.EXPLICITLY_COMPLETED, "Explicitly completed"),
    (EvidenceStatus.SUBSTANTIALLY_PROGRESSED, "Substantially progressed"),
    (
        EvidenceStatus.RESPONDED_BUT_PENDING,
        "Responded to, but the work is not confirmed complete",
    ),
    (EvidenceStatus.AWAITING_EXTERNAL_RESPONSE, "Awaiting a response from others"),
    (EvidenceStatus.OPEN_UNRESOLVED, "Open and unresolved"),
    (EvidenceStatus.POSSIBLE_FOLLOW_UP, "Possible follow-up"),
    (EvidenceStatus.NOT_DETERMINABLE, "Not determinable from the available data"),
)


@dataclass(frozen=True)
class ReportDocument:
    """The rendered report, plus what a quality check needs to judge it."""

    title: str
    markdown: str
    workstream_count: int = 0
    thread_count: int = 0
    section_titles: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.markdown.split())


def _report_type(period: Period) -> str:
    """What kind of report this is, by the window rather than by the label.

    A range that happens to cover a whole calendar month *is* a monthly report,
    and saying "custom period" over it would understate it. `is_partial`
    already compares bounds rather than `kind` for the same reason: how a
    period was constructed is not what it covers.
    """

    if period.kind is PeriodKind.WEEK:
        return "Weekly"

    if period.kind is PeriodKind.MONTH:
        return "Monthly"

    if period.is_partial:
        return "Month-to-date (partial period)"

    if (period.start, period.end) == (
        month_of(period.start).start,
        month_of(period.start).end,
    ):
        return "Monthly"

    return "Weekly" if period.days <= 7 else "Custom period"


def _note(text: str) -> str:
    return f"*{text}*"


def _evidence(ids: list[str], limit: int = 3) -> str:
    if not ids:
        return ""

    shown = ", ".join(f"`{item[-12:]}`" for item in ids[:limit])
    more = f" +{len(ids) - limit} more" if len(ids) > limit else ""

    return f" <br/>Evidence: {shown}{more}"


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items if item and item.strip()]


def _header(
    *,
    owner: str,
    mailbox: str,
    period: Period,
    inventory: ThreadInventory,
    message_count: int,
    limitations: list[str],
    generated: bool,
) -> list[str]:
    lines = [
        f"# Activity Report — {owner}",
        "",
        f"- **Mailbox:** {mailbox}",
        f"- **Reporting period:** {period.label}"
        + ("  *(partial — the period has not finished)*" if period.is_partial else ""),
        f"- **Report type:** {_report_type(period)}",
        "- **Data sources:** mailbox correspondence"
        + (", analysed narratively" if generated else ", structural analysis only"),
        f"- **Messages analysed:** {message_count}",
        f"- **Threads analysed:** {len(inventory.all)}",
        f"- **Substantive threads:** {len(inventory.substantive)} "
        f"({len(inventory.routine)} routine or automated, not described individually)",
    ]

    lines.append("- **Known limitations:**")

    if limitations:
        lines.extend(f"  - {item}" for item in limitations)
    else:
        lines.append("  - None recorded for this period.")

    return lines


def _section(number: int, title: str, body: list[str]) -> list[str]:
    return ["", f"## {number}. {title}", "", *body]


def compose(
    analysis: PeriodAnalysis,
    *,
    owner: str,
    mailbox: str,
    inventory: ThreadInventory,
    message_count: int,
    limitations: list[str] | None = None,
    weekly: list[tuple[Period, PeriodAnalysis]] | None = None,
    cross: CrossPeriodSynthesis | None = None,
    verbose: bool = False,
) -> ReportDocument:
    """Render one analysed period as a management activity report.

    `verbose` widens the per-section limits. It is what the quality gate's
    second pass sets when a report came out short against the evidence behind
    it: the analysis already holds the material, and the first pass was simply
    showing the top of each list.
    """

    limit = VERBOSE_THREAD_LIMIT if verbose else IMPORTANT_THREAD_LIMIT
    per_status = VERBOSE_ACTIONS_PER_STATUS if verbose else ACTIONS_PER_STATUS

    synthesis = analysis.synthesis
    cross = cross or CrossPeriodSynthesis()
    every_limitation = [*(limitations or []), *analysis.limitations]

    lines = _header(
        owner=owner,
        mailbox=mailbox,
        period=analysis.period,
        inventory=inventory,
        message_count=message_count,
        limitations=every_limitation,
        generated=analysis.generated,
    )

    summary = cross.executive_summary or synthesis.executive_summary
    lines += _section(
        1,
        "Executive Summary",
        _bullets(summary)
        or [_note("No narrative summary could be produced for this period.")],
    )

    lines += _section(
        2, "Major Workstreams / Business Activities", _workstreams(synthesis)
    )
    lines += _section(
        3, "Important Conversations / Threads", _conversations(analysis, limit)
    )
    lines += _section(4, "Incoming Requests and Responses", _requests(analysis, limit))
    lines += _section(5, "Actions and Outcomes", _actions(analysis, per_status))
    lines += _section(6, "Pending / Follow-up Items", _follow_ups(synthesis))
    lines += _section(7, "Deadlines and Important Dates", _deadlines(analysis))
    lines += _section(8, "Decisions / Outcomes", _decisions(analysis))
    lines += _section(9, "Issues, Risks, and Blockers", _risks(synthesis))
    lines += _section(
        10, "Key People / Stakeholders", _stakeholders(inventory, verbose)
    )
    lines += _section(
        11,
        "Communication and Workload Interpretation",
        _workload(synthesis, inventory, message_count),
    )

    if weekly:
        lines += _section(12, "Weekly Progression", _weekly(weekly, cross))
        lines += _section(13, "Period-End Status", _period_end(analysis))
        lines += _section(
            14,
            "Management-Level Summary",
            _bullets(cross.management_summary or synthesis.management_summary)
            or [_note("No management observations could be produced.")],
        )
    else:
        lines += _section(12, "Period-End Status", _period_end(analysis))
        lines += _section(
            13,
            "Management-Level Summary",
            _bullets(synthesis.management_summary)
            or [_note("No management observations could be produced.")],
        )

    markdown = "\n".join(lines).rstrip() + "\n"

    return ReportDocument(
        title=f"Activity Report — {owner} — {analysis.period.label}",
        markdown=markdown,
        workstream_count=len(synthesis.workstreams),
        thread_count=len(analysis.findings),
        section_titles=[
            line.split(". ", 1)[-1] for line in lines if line.startswith("## ")
        ],
    )


def _workstreams(synthesis) -> list[str]:
    if not synthesis.workstreams:
        return [
            _note(
                "No workstreams could be derived from the available evidence. "
                "Conversations are listed individually in section 3."
            )
        ]

    body: list[str] = []

    for stream in synthesis.workstreams:
        body += [
            f"### {stream.name}",
            "",
            f"- **Scope:** {stream.purpose or 'Not stated in the correspondence.'}",
            "- **Stakeholders:** "
            + (", ".join(stream.stakeholders) or "Not identified."),
            f"- **Activity:** {stream.activity or 'Not described.'}",
            f"- **Owner's contribution:** {stream.owner_contribution or _UNKNOWN}",
            f"- **Current status:** {stream.status or _UNKNOWN}",
            f"- **Next step:** {stream.next_step or _NO_NEXT_STEP}",
        ]

        if stream.risks:
            body.append(f"- **Risks / dependencies:** {'; '.join(stream.risks)}")

        body.append(
            f"- **Evidence:** {len(stream.evidence)} message(s), "
            f"{len(stream.thread_ids)} thread(s)"
        )
        body.append("")

    return body


def _conversations(analysis: PeriodAnalysis, limit: int) -> list[str]:
    findings = [f for f in analysis.findings if f.summary][:limit]

    if not findings:
        return [_note("No substantive conversations were identified in this period.")]

    rows = [
        "| Conversation | Why it matters | Progression | Status |",
        "|---|---|---|---|",
    ]

    for finding in findings:
        rows.append(
            f"| {_cell(finding.topic)} "
            f"| {_cell(finding.significance) or '—'} "
            f"| {_cell(finding.summary)} "
            f"| {finding.status.value.replace('_', ' ')}"
            f"{_evidence(finding.evidence, 2)} |"
        )

    return rows


def _cell(text: str | None) -> str:
    """Table-safe: a pipe in a subject line would split the row."""

    return (text or "").replace("|", "/").replace("\n", " ").strip()


def _requests(analysis: PeriodAnalysis, limit: int) -> list[str]:
    incoming = [
        finding
        for finding in analysis.findings
        if finding.other_actions and finding.summary
    ][:limit]

    if not incoming:
        return [
            _note(
                "No substantive incoming requests were separately identified. "
                "Section 3 lists the period's conversations."
            )
        ]

    rows = ["| Request | Raised in | Response / current status |", "|---|---|---|"]

    for finding in incoming:
        rows.append(
            f"| {_cell('; '.join(finding.other_actions[:2]))} "
            f"| {_cell(finding.topic)} "
            f"| {_cell(finding.outcome) or finding.status.value.replace('_', ' ')} |"
        )

    return rows


def _actions(analysis: PeriodAnalysis, per_status: int) -> list[str]:
    body: list[str] = []

    for status, heading in _STATUS_HEADINGS:
        matching = [f for f in analysis.findings if f.status is status]

        body += ["", f"### {heading}", ""]

        if not matching:
            body.append(_note("Nothing in this period fell into this category."))
            continue

        for finding in matching[:per_status]:
            detail = finding.outcome or finding.summary or finding.topic
            body.append(
                f"- **{finding.topic}** — {_cell(detail)}"
                + _evidence(finding.evidence, 2)
            )

        if len(matching) > per_status:
            body.append(f"- …and {len(matching) - per_status} more.")

    return body


def _follow_ups(synthesis) -> list[str]:
    if not synthesis.follow_ups:
        return [
            _note(
                "No follow-up commitments were identified from the "
                "correspondence in this period."
            )
        ]

    rows = [
        "| Item | Responsible | Deadline | Next action |",
        "|---|---|---|---|",
    ]

    for item in synthesis.follow_ups:
        rows.append(
            f"| {_cell(item.item)} "
            f"| {_cell(item.responsible) or 'Not stated'} "
            f"| {item.deadline or 'None stated'} "
            f"| {_cell(item.next_action) or 'Not stated'}"
            f"{_evidence(item.evidence, 2)} |"
        )

    return rows


def _deadlines(analysis: PeriodAnalysis) -> list[str]:
    dated = [f for f in analysis.findings if f.deadline]

    if not dated:
        return [
            _note(
                "No dates were explicitly stated in the correspondence for this "
                "period. Nothing here is inferred from tone or urgency, and no "
                "meeting attendance is inferred from an invitation."
            )
        ]

    rows = ["| Date | Context | Evidence |", "|---|---|---|"]

    for finding in sorted(dated, key=lambda f: f.deadline or ""):
        rows.append(
            f"| {finding.deadline} | {_cell(finding.topic)} "
            f"|{_evidence(finding.evidence, 2)} |"
        )

    return rows


def _decisions(analysis: PeriodAnalysis) -> list[str]:
    synthesis = analysis.synthesis
    body: list[str] = ["### Explicit decisions and confirmed outcomes", ""]

    completed = [
        f
        for f in analysis.findings
        if f.status is EvidenceStatus.EXPLICITLY_COMPLETED and f.outcome
    ]
    decided = _bullets([item.decision for item in synthesis.decisions])
    decided += [f"- {f.outcome}{_evidence(f.evidence, 2)}" for f in completed[:6]]

    body += decided or [
        _note("No decision was explicitly recorded in the available correspondence.")
    ]

    body += ["", "### Explicitly not decided / unresolved", ""]
    body += _bullets([item.question for item in synthesis.unresolved]) or [
        _note("No unresolved decision points were identified.")
    ]

    return body


def _risks(synthesis) -> list[str]:
    if not synthesis.risks:
        return [
            _note(
                "No risks were identified from the available evidence. Nothing "
                "is listed here to fill the section."
            )
        ]

    rows = ["| Issue | Impact | Status at period end |", "|---|---|---|"]

    for risk in synthesis.risks:
        rows.append(
            f"| {_cell(risk.issue)} | {_cell(risk.impact) or '—'} "
            f"| {_cell(risk.status) or '—'}{_evidence(risk.evidence, 2)} |"
        )

    return rows


def _stakeholders(inventory: ThreadInventory, verbose: bool = False) -> list[str]:
    by_org: dict[str, set[str]] = {}

    for thread in inventory.substantive:
        for person in thread.counterparties[:6]:
            from app.services.features.reports.classification import organisation_of

            org = organisation_of(person.domain) or "Individual / other"
            by_org.setdefault(org, set()).add(person.display)

    if not by_org:
        return [_note("No external stakeholders were identified in this period.")]

    ordered = sorted(by_org.items(), key=lambda item: -len(item[1]))[
        : 30 if verbose else 12
    ]

    return [
        f"- **{org}:** {', '.join(sorted(people)[:8])}"
        + (f" (+{len(people) - 8} more)" if len(people) > 8 else "")
        for org, people in ordered
    ]


def _workload(synthesis, inventory: ThreadInventory, message_count: int) -> list[str]:
    automated = inventory.automated_message_count
    substantive_threads = len(inventory.substantive)

    context = (
        f"{message_count} message(s) across {len(inventory.all)} conversation(s). "
        f"{substantive_threads} conversation(s) carried substantive business content; "
        f"{len(inventory.routine)} were routine or automated, accounting for "
        f"{automated} message(s). Volume is context here, not a measure of output."
    )

    body = [context]

    if synthesis.workload:
        body += ["", synthesis.workload]

    return body


def _period_end(analysis: PeriodAnalysis) -> list[str]:
    groups = {
        "Completed or substantially completed": (
            EvidenceStatus.EXPLICITLY_COMPLETED,
            EvidenceStatus.SUBSTANTIALLY_PROGRESSED,
        ),
        "Still open or pending": (
            EvidenceStatus.OPEN_UNRESOLVED,
            EvidenceStatus.RESPONDED_BUT_PENDING,
        ),
        "Awaiting external parties": (EvidenceStatus.AWAITING_EXTERNAL_RESPONSE,),
        "Unclear from the available evidence": (
            EvidenceStatus.NOT_DETERMINABLE,
            EvidenceStatus.POSSIBLE_FOLLOW_UP,
        ),
    }

    body: list[str] = []

    for heading, statuses in groups.items():
        matching = [f for f in analysis.findings if f.status in statuses]
        body += ["", f"### {heading}", ""]

        if not matching:
            body.append(_note("Nothing in this period."))
            continue

        body.append(f"{len(matching)} conversation(s):")
        body += [f"- {f.topic}" for f in matching[:6]]

        if len(matching) > 6:
            body.append(f"- …and {len(matching) - 6} more.")

    return body


def _weekly(
    weekly: list[tuple[Period, PeriodAnalysis]], cross: CrossPeriodSynthesis
) -> list[str]:
    body: list[str] = []

    for period, analysis in weekly:
        body += ["", f"### {period.label}", ""]

        bullets = _bullets(analysis.synthesis.executive_summary[:5])
        body += bullets or [
            _note(
                f"{len(analysis.findings)} conversation(s) were active in this "
                "week; no narrative summary was produced for it."
            )
        ]

    if cross.carried_forward:
        body += ["", "### Carried forward between weeks", ""]
        body += [
            f"- **{item.item}** — from {item.from_week}; {item.status}"
            for item in cross.carried_forward
        ]

    if cross.themes:
        body += ["", "### Recurring themes across the period", ""]
        body += _bullets(cross.themes)

    return body
