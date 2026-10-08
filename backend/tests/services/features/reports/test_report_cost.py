"""What one report is allowed to spend, and what it does when it runs out.

These are regression tests for a report that took minutes and could in
principle have taken hours. Three compounding causes, all of them measured
against a stub provider before being fixed:

1. Every conversation was analysed twice — once inside its week, once as part
   of the month — so a month of the August corpus made 18 model calls where 12
   carry the same information.
2. Nothing bounded the total. The calls run in sequence, so the response time
   was the provider's latency multiplied by the number of conversations, with
   no ceiling of any kind.
3. Each call inherited the OpenAI SDK's defaults: a 600-second read timeout and
   two automatic retries. One unanswered call could hold the request for half
   an hour, and 18 of them for most of a day.

So the tests below count calls and assert on a budget rather than on wording.
A report that quietly starts costing twice as much fails here.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.features.reports import analysis as analysis_stage
from app.services.features.reports import pipeline
from app.services.features.reports.analysis import Budget
from app.services.features.reports.correspondence import (
    Correspondence,
    Direction,
    Message,
    Participant,
)
from app.services.features.reports.period import month_of, week_of

OWNER = "owner@sunradia.com"
INSIDE = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)

#: Enough conversations to need several batches, spread over a month so the
#: pipeline splits it into weeks. The numbers are what these tests are about:
#: 40 threads is several batches (THREADS_PER_BATCH each), and five weekly
#: segments.
THREADS = 40


class Counter:
    """Stands in for `analysis_engine.run` and counts what it was asked."""

    def __init__(self, *, seconds: float = 0.0, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.seconds = seconds
        self.fail = fail

    def __call__(self, prompt_name: str, response_model: type, **variables: object):
        self.calls.append(prompt_name)

        if self.seconds:
            time.sleep(self.seconds)

        if self.fail:
            raise RuntimeError("the provider said no")

        return response_model()

    @property
    def thread_calls(self) -> int:
        return self.calls.count("report_thread_analysis")


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch):
    def install(**kwargs) -> Counter:
        counter = Counter(**kwargs)
        monkeypatch.setattr(analysis_stage.analysis_engine, "run", counter)

        return counter

    return install


def _exchange(index: int) -> list[Message]:
    """One conversation: a question and the owner's reply, three days apart."""

    occurred = INSIDE + timedelta(days=index % 25, hours=index % 7)
    common = {
        "thread_id": f"thread-{index}",
        "subject": f"Site {index}: revised delivery schedule",
        "cc": [],
        "has_attachments": False,
    }

    return [
        Message(
            message_id=f"in-{index}",
            body="Could you confirm the milestone dates before the site visit?",
            sender=Participant(address=f"client{index}@acme.example"),
            recipients=[Participant(address=OWNER)],
            occurred_at=occurred,
            direction=Direction.INBOUND,
            **common,
        ),
        Message(
            message_id=f"out-{index}",
            body="Confirmed. The revised dates and the updated plan are attached.",
            sender=Participant(address=OWNER),
            recipients=[Participant(address=f"client{index}@acme.example")],
            occurred_at=occurred + timedelta(hours=4),
            direction=Direction.OUTBOUND,
            **common,
        ),
    ]


@pytest.fixture
def busy_month() -> tuple[Correspondence, object]:
    """A month of correspondence, built rather than read.

    Synthetic on purpose: these tests assert call *counts*, and a fixture read
    from `report_data/` would make them depend on a corpus that a clean
    checkout does not have.
    """

    period = month_of(INSIDE)
    messages = [m for index in range(THREADS) for m in _exchange(index)]

    return (
        Correspondence(
            mailbox=OWNER, owner_name="Owner", period=period, messages=messages
        ),
        period,
    )


class TestOnePassOverTheConversations:
    def test_a_week_inside_a_month_is_not_analysed_twice(
        self, busy_month, counted
    ) -> None:
        """The expensive stage runs once, whatever it is later read as.

        A thread in the first week of August is the same thread whether it is
        being described as part of that week or of the month, so analysing it
        again for each is work with no new information in it.
        """

        correspondence, period = busy_month
        counter = counted()

        result = pipeline.generate(
            correspondence, period=period, owner="Owner", use_model=True
        )

        threads = len(result.inventory.substantive)
        expected = -(
            -min(threads, analysis_stage.MAX_THREADS_ANALYSED)
            // analysis_stage.THREADS_PER_BATCH
        )

        assert result.weekly, "a month should still be reported week by week"
        assert counter.thread_calls == expected

    def test_every_week_still_gets_its_own_synthesis(self, busy_month, counted) -> None:
        """Reuse is of findings, not of conclusions.

        Each week is still read on its own — that is what the weekly sections
        are — and only the per-conversation analysis behind them is shared.
        """

        correspondence, period = busy_month
        counter = counted()

        result = pipeline.generate(
            correspondence, period=period, owner="Owner", use_model=True
        )

        # One per week that had conversations in it, plus the month's own. A
        # week with nothing in it asks the model nothing, which is why this
        # counts weeks with findings rather than segments.
        active = [item for _, item in result.weekly if item.findings]

        assert counter.calls.count("report_period_synthesis") == len(active) + 1
        assert len(active) >= 3

    def test_a_single_week_is_not_split_at_all(self, busy_month, counted) -> None:
        correspondence, _ = busy_month
        period = week_of(INSIDE)
        counter = counted()

        result = pipeline.generate(
            correspondence, period=period, owner="Owner", use_model=True
        )

        assert result.weekly == []
        assert counter.calls.count("report_cross_period") == 0


class TestTheBudget:
    def test_the_report_stops_calling_the_model_when_the_budget_is_spent(
        self, busy_month, counted
    ) -> None:
        """The bound is on the total, not on each call.

        Each individual call being reasonable is exactly how the original
        problem happened: eighteen reasonable calls in sequence is not a
        reasonable request.
        """

        correspondence, period = busy_month
        counter = counted(seconds=0.05)

        started = time.monotonic()
        result = pipeline.generate(
            correspondence,
            period=period,
            owner="Owner",
            use_model=True,
            budget_seconds=0.1,
        )
        elapsed = time.monotonic() - started

        assert elapsed < 2.0
        # Four batches at 0.05s each against a 0.1s budget: the loop must stop
        # partway, not run to the end and notice afterwards.
        assert counter.thread_calls <= 3

        # And the reader is told, rather than being shown a thin report with no
        # explanation for why it is thin.
        assert any("time allowed" in note for note in result.analysis.limitations)

    def test_an_already_spent_budget_calls_the_model_not_at_all(
        self, busy_month, counted
    ) -> None:
        """The check is before each batch, not after the loop.

        A budget noticed only at the end is a report of how long it took, not a
        bound on it.
        """

        from app.services.features.reports.threads import take_inventory

        correspondence, period = busy_month
        counter = counted()

        analysis = analysis_stage.analyse(
            take_inventory(correspondence),
            period=period,
            owner="Owner",
            use_model=True,
            budget=Budget(0.0),
        )

        assert counter.calls == []
        assert analysis.findings, "a spent budget still describes every thread"
        assert analysis.generated is False

    def test_a_spent_budget_still_produces_a_whole_report(
        self, busy_month, counted
    ) -> None:
        correspondence, period = busy_month
        counted(seconds=0.05)

        result = pipeline.generate(
            correspondence,
            period=period,
            owner="Owner",
            use_model=True,
            budget_seconds=0.1,
        )

        assert result.document.word_count > 300
        assert result.document.thread_count > 0

    def test_no_budget_means_no_bound(self, busy_month, counted) -> None:
        """The offline path is unchanged.

        `scripts/` runs the pipeline with no deadline on purpose; only the
        request path, where somebody is waiting, carries one.
        """

        correspondence, period = busy_month
        counter = counted()

        pipeline.generate(correspondence, period=period, owner="Owner", use_model=True)

        assert counter.thread_calls > 0

    def test_a_budget_is_never_exceeded_by_a_failing_provider(
        self, busy_month, counted
    ) -> None:
        """A failed batch must not become a retry loop.

        Every batch fails here. The pipeline may fall back to structure, but it
        may not call again for the same threads.
        """

        correspondence, period = busy_month
        counter = counted(fail=True)

        result = pipeline.generate(
            correspondence, period=period, owner="Owner", use_model=True
        )

        threads = len(result.inventory.substantive)
        expected = -(
            -min(threads, analysis_stage.MAX_THREADS_ANALYSED)
            // analysis_stage.THREADS_PER_BATCH
        )

        assert counter.thread_calls == expected
        assert result.document.word_count > 300


class TestTheBudgetItself:
    def test_an_unspent_budget_does_not_report_itself_as_spent(self) -> None:
        budget = Budget(60.0)

        assert budget.spent() is False
        assert budget.exhausted is False

    def test_a_zero_budget_is_spent_immediately(self) -> None:
        budget = Budget(0.0)

        assert budget.spent() is True
        assert budget.exhausted is True

    def test_none_means_unbounded(self) -> None:
        assert Budget(None).spent() is False


class TestWhatAFailedBatchLogs:
    def test_a_failed_batch_names_its_exception_and_carries_a_traceback(
        self, busy_month, counted, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`report_thread_batch_failed` on its own is not a diagnosis.

        The line used to put the exception type in an `extra` dict, which the
        default formatter does not print, so a provider outage, a malformed
        response and a bug in this module were indistinguishable in a log.
        """

        correspondence, period = busy_month
        counted(fail=True)

        with caplog.at_level("WARNING"):
            pipeline.generate(
                correspondence, period=period, owner="Owner", use_model=True
            )

        failures = [
            record
            for record in caplog.records
            if record.getMessage().startswith("report_thread_batch_failed")
        ]

        assert failures
        assert "RuntimeError" in failures[0].getMessage()
        assert "batch=1/" in failures[0].getMessage()
        assert failures[0].exc_info is not None

    def test_a_log_line_carries_no_correspondence(
        self, busy_month, counted, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Counts and identifiers, never contents.

        A report's logs describe other people's mail. Subjects, bodies and
        addresses do not belong in them at any level.
        """

        correspondence, period = busy_month
        counted(fail=True)

        with caplog.at_level("DEBUG"):
            pipeline.generate(
                correspondence, period=period, owner="Owner", use_model=True
            )

        bodies = [
            message.body[:40]
            for message in correspondence.messages[:20]
            if len(message.body) > 40
        ]
        logged = "\n".join(record.getMessage() for record in caplog.records)

        for excerpt in bodies:
            assert excerpt not in logged

        for message in correspondence.messages[:20]:
            assert message.subject not in logged
