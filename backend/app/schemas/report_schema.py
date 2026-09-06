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
