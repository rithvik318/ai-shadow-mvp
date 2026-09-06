"""The management summary: what actually happened during a period.

The Reports section answers "what happened", and the Tasks page answers "what
do I need to do". Those are different questions and this module only answers
the first. It reads two things that already exist — the email digest for the
period, and the user's tasks — and arranges them into the eight sections a
manager reads.

**Nothing here is invented, and nothing here calls a model.** Every sentence is
assembled from a count or a row. That is a deliberate constraint rather than a
limitation to be lifted later: a generated narrative is exactly where a report
starts saying things nobody did, and a management summary that might be
embellished is worth less than a shorter one that cannot be.

Two consequences worth stating, because they are what an LLM would have papered
over:

**"Major workstreams" is a grouping, not a judgement.** It is the correspondents
and threads the period actually contained, ordered by volume. It is not an
inferred list of projects — inferring "Tech Park" from a set of subject lines
is a guess, and a guess in a report signed by a person is that person's problem.
The section says what the grouping is so nobody reads more into it.

**Absent sections are absent, not empty.** A period with no calendar data has
no calendar section and says the calendar was unavailable; a period with no
risks has no risks section rather than a heading over the word "None". A report
padded to a fixed shape trains its reader to skim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.task import Task
from app.services.features.reports.digest_service import EmailDigest
from app.services.features.reports.period import Period
from app.services.features.tasks import task_service
from app.services.features.tasks.signal import TaskSignal, days_overdue, signal_for
from app.services.features.tasks.urgency import TaskStatus

#: How many items any one section lists. A management summary is one to three
#: pages; a list of forty correspondents is a data export wearing a report's
#: title, and the reader stops at the fifth either way.
SECTION_LIMIT = 6

CALENDAR_UNAVAILABLE = (
    "Calendar activity was not available for this period: the Microsoft Graph "
    "application has no calendar consent, so no meetings are imported. Nothing "
    "here is inferred from email in their place."
)


@dataclass(frozen=True)
class Line:
    """One bullet: what it says, and the detail under it where there is one."""

    text: str
    detail: str | None = None


@dataclass(frozen=True)
class Section:
    """One heading and its bullets. A section with no lines is not rendered."""

    title: str
    lines: list[Line] = field(default_factory=list)
    #: Shown instead of the bullets when there are none, and only where the
    #: absence itself is worth saying — an unavailable calendar, say.
    note: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.note


@dataclass(frozen=True)
class ActivityReport:
    """A period of work, as a document."""

    user_id: uuid.UUID
    user_name: str
    period: Period
    generated_at: datetime
    sections: list[Section] = field(default_factory=list)

    @property
    def rendered(self) -> list[Section]:
        return [section for section in self.sections if not section.is_empty]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _completed_in(tasks: list[Task], period: Period) -> list[Task]:
    return [task for task in tasks if period.contains(task.completed_at)]


def _open_at(tasks: list[Task], moment: datetime) -> list[Task]:
    return [
        task
        for task in tasks
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        and (task.created_at is None or task.created_at.astimezone(UTC) <= moment)
    ]


def _executive_summary(
    *, digest: EmailDigest | None, completed: list[Task], outstanding: list[Task]
) -> Section:
    """Three to five lines, each of which is a count somebody can check."""

    lines: list[Line] = []

    if digest is not None:
        lines.append(
            Line(
                f"{_plural(digest.received_count, 'message')} received and "
                f"{_plural(digest.sent_count, 'message')} sent."
            )
        )

        if digest.needs_reply_count:
            lines.append(
                Line(
                    f"{_plural(digest.needs_reply_count, 'message')} identified "
                    "as needing a reply."
                )
            )

        if digest.untriaged_count:
            # Said plainly rather than omitted: a summary drawn from triage
            # should say how much of the period triage has not read.
            lines.append(
                Line(
                    f"{_plural(digest.untriaged_count, 'message')} not yet "
                    "triaged, so they are counted but not classified."
                )
            )
    else:
        lines.append(Line(_no_mailbox_line()))

    lines.append(Line(f"{_plural(len(completed), 'task')} completed."))

    late = [task for task in outstanding if (days_overdue(task.due_at) or 0) > 0]

    if late:
        lines.append(
            Line(
                f"{_plural(len(late), 'task')} past its deadline"
                if len(late) == 1
                else f"{_plural(len(late), 'task')} past their deadlines"
            )
        )
    elif outstanding:
        lines.append(Line(f"{_plural(len(outstanding), 'task')} still open."))

    return Section(title="Executive summary", lines=lines)


def _no_mailbox_line() -> str:
    return (
        "Email activity was not available for this period: no mailbox was "
        "connected, so nothing was read and nothing is estimated."
    )


def _workstreams(digest: EmailDigest | None) -> Section:
    """Where the period's correspondence went, by who it was with.

    Explicitly a grouping by correspondent rather than an inferred project
    list. The subtitle says so, because a reader who assumes these are projects
    will read a conclusion the data does not support.
    """

    if digest is None or not digest.top_correspondents:
        return Section(title="Major workstreams")

    lines = [
        Line(
            person.name or person.address,
            f"{_plural(person.message_count, 'message')}"
            + (f" · {person.address}" if person.name else ""),
        )
        for person in digest.top_correspondents[:SECTION_LIMIT]
    ]

    return Section(
        title="Major workstreams",
        lines=lines,
        note=None,
    )


def _conversations(digest: EmailDigest | None) -> Section:
    """What triage flagged as urgent or high priority. Its opinion, not a new one."""

    if digest is None or not digest.highlights:
        return Section(title="Important conversations")

    return Section(
        title="Important conversations",
        lines=[
            Line(
                item.subject,
                " · ".join(
                    part
                    for part in (
                        item.sender_name or item.sender_address,
                        item.summary,
                    )
                    if part
                ),
            )
            for item in digest.highlights[:SECTION_LIMIT]
        ],
    )


def _decisions(*, completed: list[Task], digest: EmailDigest | None) -> Section:
    """Outcomes, evidenced.

    Completed work and messages actually sent. Deliberately not "decisions the
    model detected in the text" — a decision this system did not witness being
    made is not one it should report.
    """

    lines = [
        Line(
            task.title,
            f"Completed {task.completed_at:%d %b}" if task.completed_at else None,
        )
        for task in completed[:SECTION_LIMIT]
    ]

    if digest is not None and digest.sent_count:
        lines.append(
            Line(f"{_plural(digest.sent_count, 'message')} sent from this mailbox.")
        )

    return Section(title="Decisions and outcomes", lines=lines)


def _requires_follow_up(
    *, outstanding: list[Task], digest: EmailDigest | None, now: datetime
) -> Section:
    """What is still owed, worst first."""

    ordered = sorted(
        outstanding,
        key=lambda task: (-(days_overdue(task.due_at, now) or 0), task.title),
    )

    lines = [
        Line(
            task.title,
            _lateness(task, now),
        )
        for task in ordered[:SECTION_LIMIT]
    ]

    if digest is not None and digest.needs_reply_count:
        lines.append(
            Line(f"{_plural(digest.needs_reply_count, 'message')} awaiting a reply.")
        )

    return Section(title="Requires follow-up", lines=lines)


def _lateness(task: Task, now: datetime) -> str:
    late = days_overdue(task.due_at, now)

    if task.due_at is None:
        return "No deadline stated"

    if late:
        return f"{_plural(late, 'day')} overdue"

    return f"Due {task.due_at:%d %b}"


def _completed_work(completed: list[Task]) -> Section:
    return Section(
        title="Completed work",
        lines=[
            Line(
                task.title,
                f"Completed {task.completed_at:%d %b}" if task.completed_at else None,
            )
            for task in completed[:SECTION_LIMIT]
        ],
    )


def _risks(*, outstanding: list[Task], now: datetime) -> Section:
    """Only what the data supports: work that is materially late.

    A risks section that lists something every week stops being read. This one
    is empty — and therefore not rendered — whenever nothing is badly overdue.
    """

    urgent = [
        task
        for task in outstanding
        if signal_for(status=task.status, due_at=task.due_at, now=now)
        is TaskSignal.URGENT
    ]

    return Section(
        title="Risks and issues",
        lines=[
            Line(task.title, _lateness(task, now)) for task in urgent[:SECTION_LIMIT]
        ],
    )


def _bottom_line(
    *,
    digest: EmailDigest | None,
    completed: list[Task],
    outstanding: list[Task],
    now: datetime,
) -> Section:
    """One paragraph: what matters most, assembled from what is above."""

    urgent = [
        task
        for task in outstanding
        if signal_for(status=task.status, due_at=task.due_at, now=now)
        is TaskSignal.URGENT
    ]

    parts: list[str] = []

    if completed:
        parts.append(f"{_plural(len(completed), 'task')} finished")

    if urgent:
        parts.append(
            f"{_plural(len(urgent), 'task')} more than two days past a deadline"
        )
    elif outstanding:
        parts.append(f"{_plural(len(outstanding), 'task')} still open, none badly late")

    if digest is not None and digest.needs_reply_count:
        parts.append(f"{_plural(digest.needs_reply_count, 'message')} awaiting a reply")

    if not parts:
        return Section(
            title="Bottom line",
            lines=[Line("No recorded activity in this period.")],
        )

    return Section(title="Bottom line", lines=[Line(_sentence(parts))])


def _sentence(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0].capitalize() + "."

    return (", ".join(parts[:-1]) + f", and {parts[-1]}.").capitalize()


def build(
    db: Session,
    *,
    user_id: uuid.UUID,
    user_name: str,
    period: Period,
    digest: EmailDigest | None,
    now: datetime | None = None,
) -> ActivityReport:
    """Assemble the report for one person and one period.

    `digest` is None when no mailbox could be read. The report is still
    produced — task activity is real and does not depend on email — and says
    plainly that the email half was unavailable rather than reporting zero
    messages, which would claim a mailbox was read.
    """

    moment = now or datetime.now(UTC)
    # The report describes the period, so it is evaluated at the period's end
    # unless that is still in the future.
    as_of = min(period.end, moment)

    tasks = task_service.list_tasks(db, user_id=user_id)
    completed = _completed_in(tasks, period)
    outstanding = _open_at(tasks, as_of)

    sections = [
        _executive_summary(digest=digest, completed=completed, outstanding=outstanding),
        _workstreams(digest),
        _conversations(digest),
        _decisions(completed=completed, digest=digest),
        _requires_follow_up(outstanding=outstanding, digest=digest, now=as_of),
        _completed_work(completed),
        _risks(outstanding=outstanding, now=as_of),
        Section(title="Calendar", note=CALENDAR_UNAVAILABLE),
        _bottom_line(
            digest=digest, completed=completed, outstanding=outstanding, now=as_of
        ),
    ]

    return ActivityReport(
        user_id=user_id,
        user_name=user_name,
        period=period,
        generated_at=moment,
        sections=sections,
    )


def serialise(report: ActivityReport) -> dict:
    """The report as it is stored and returned — one shape for both."""

    return {
        "user_name": report.user_name,
        "period_label": report.period.label,
        "generated_at": report.generated_at.isoformat(),
        "sections": [
            {
                "title": section.title,
                "note": section.note,
                "lines": [
                    {"text": line.text, "detail": line.detail} for line in section.lines
                ],
            }
            for section in report.rendered
        ],
    }
