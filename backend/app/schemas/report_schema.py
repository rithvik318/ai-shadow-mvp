"""The shapes the Reports workspace reads.

One idea holds this module together: **a stored report and a freshly generated
one are the same shape.** `ReportEnvelope.content` carries either the JSON that
was written down when the period closed or the JSON just built from live data,
and the client cannot tell — nor should it need to, because the difference is
about *when*, and `generated_at` and `is_provisional` already say that.

That is why `content` is typed as a plain object rather than a union of the
three report bodies. A snapshot written by this build must still be readable by
the next one; pinning stored history to a response model would mean every
future field addition either breaks old rows or silently drops data out of
them. The three bodies below are documented and returned, and validation
happens where a report is *built*, not where one is read back.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.models.report import ReportStatus, ReportType
from app.services.features.reports.period import PeriodKind


class PeriodResponse(BaseModel):
    """A window, named the way a person selects it."""

    kind: PeriodKind
    #: The stable identity — `2026-08-31` for a week, `2026-08` for a month.
    #: This is what a client sends back to ask for that period again.
    key: str
    label: str
    start: datetime
    #: Exclusive. `start <= t < end`.
    end: datetime
    #: False while the period is still running.
    is_complete: bool


class CorrespondentResponse(BaseModel):
    address: str
    name: str | None = None
    message_count: int


class DigestMessageResponse(BaseModel):
    """One message in a digest, with triage's verdict where triage ran."""

    message_id: str
    subject: str
    sender_name: str | None = None
    sender_address: str | None = None
    received_at: datetime | None = None
    #: `untriaged` where nobody has classified this message. Never guessed.
    category: str
    priority: str | None = None
    summary: str | None = None
    needs_reply: bool = False


class EmailDigestBody(BaseModel):
    """What actually arrived and left over one period.

    Every count is of messages the mailbox returned inside the window.
    `truncated` says when the page bound may have cut the window short — a
    partial count that says so is useful, and one that does not is a wrong
    total.
    """

    mailbox: str | None = None

    received_count: int
    sent_count: int
    triaged_count: int
    untriaged_count: int
    needs_reply_count: int
    follow_up_count: int
    #: Messages the provider returned with no timestamp, which are counted in
    #: no period rather than being swept into this one.
    undated_count: int

    by_category: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    top_correspondents: list[CorrespondentResponse] = Field(default_factory=list)
    needs_reply: list[DigestMessageResponse] = Field(default_factory=list)
    highlights: list[DigestMessageResponse] = Field(default_factory=list)

    #: True when the mailbox was read and genuinely held nothing. Distinct from
    #: an `unavailable` report, where no mailbox was read at all.
    is_quiet: bool = False

    truncated: bool = False
    truncation_detail: str | None = None


class ReportEnvelope(BaseModel):
    """One report: what it is, when it covers, and the report itself."""

    report_type: ReportType
    status: ReportStatus
    period: PeriodResponse
    generated_at: datetime

    #: True when this was generated over a period that had not finished. A
    #: provisional report is regenerated on the next request; a final one is
    #: read back exactly as it was written.
    is_provisional: bool

    #: True when this came from the store rather than from live data. The one
    #: fact the UI needs to explain why a report of a past week does not move.
    from_history: bool = False

    #: Why an `unavailable` report could not be produced, in words. Never a
    #: token, an address or a stack trace.
    detail: str | None = None

    #: The report body. `WeeklyReportResponse` for `weekly_work`,
    #: `EmailDigestBody` for the two digests, and `{}` when `status` is
    #: `unavailable` — deliberately empty rather than zero-filled, because
    #: zeroes would read as a measurement nobody took.
    content: dict = Field(default_factory=dict)


class ReportSummaryResponse(BaseModel):
    """One row in the history list: enough to pick a report, not the report."""

    report_type: ReportType
    status: ReportStatus
    period: PeriodResponse
    generated_at: datetime
    is_provisional: bool
    detail: str | None = None


class ReportHistoryResponse(BaseModel):
    """Everything a person can open, stored and not yet stored.

    `available_periods` is deliberately not "the periods with a row". It is
    what the calendar offers, so a person can ask for a week nobody has
    generated yet and get one built on request. A selector driven only by
    stored rows would be empty on the first ever visit.
    """

    report_type: ReportType
    items: list[ReportSummaryResponse] = Field(default_factory=list)
    available_periods: list[PeriodResponse] = Field(default_factory=list)
    total: int = 0


class ActivitySource(StrEnum):
    """How this reply was produced. Four states a reader can act on.

    `stored` and `revalidated` both mean the document was not rebuilt — the
    difference is whether the mailbox was re-read to confirm it — and
    `generated` means it was built for this request. A report that is being
    built is not this model at all: see `ActivityReportPendingResponse`.
    """

    STORED = "stored"
    REVALIDATED = "revalidated"
    GENERATED = "generated"


class ActivityReportPendingResponse(BaseModel):
    """Somebody is already building this exact report.

    Returned with `202 Accepted` when a second request arrives for a window a
    generation is already running for. It carries the claim's age so a client
    can say how long it has been going rather than showing a spinner with no
    end in sight.
    """

    state: Literal["generating"] = "generating"
    period: PeriodResponse
    period_label: str
    started_at: datetime
    elapsed_seconds: float
    detail: str = (
        "This report is being generated. It will be stored when it finishes, "
        "and every request after that reads it from storage."
    )


class ActivityReportResponse(BaseModel):
    """A detailed activity report, plus what it was built from.

    The metadata is not decoration. A reader needs to know whether a narrative
    analysis contributed, how much of the mailbox was substantive, and whether
    the quality check was satisfied — otherwise a thin report and a quiet month
    look identical, which is the confusion the previous report created.

    `source`, `generated_at` and `data_through` answer the other question a
    reader of a stored report has: how old is this, and how old is the evidence
    under it. They are different instants and both are shown.
    """

    state: Literal["report"] = "report"
    source: ActivitySource
    report_version: str
    is_provisional: bool = Field(
        description=(
            "True when the window was still open when this was generated, so "
            "the report may change as more mail arrives."
        )
    )
    generated_at: datetime
    checked_at: datetime | None = Field(
        default=None,
        description=(
            "When the evidence behind this report was last confirmed current. "
            "Later than `generated_at` for a report revalidated against an "
            "unchanged mailbox."
        ),
    )
    data_through: datetime | None = Field(
        default=None,
        description="The instant the correspondence behind this report runs to.",
    )

    period: PeriodResponse
    period_label: str
    is_partial: bool = Field(
        description="True when the window stops short of the calendar period."
    )

    title: str
    markdown: str

    word_count: int
    sections: list[str]
    workstream_count: int
    thread_count: int
    messages_analysed: int
    substantive_threads: int
    routine_threads: int

    narrative: bool = Field(
        description=(
            "Whether a language model contributed. False means the report "
            "describes conversations structurally rather than by content."
        )
    )
    quality_ok: bool
    quality_notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
