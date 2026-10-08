"""The evidence-based report pipeline, from messages to document.

The old report was assembled from an `EmailDigest` — counts, a category tally
and a list of correspondents — so the best it could say was how many messages
arrived. These tests pin the properties that replace that: real threads, real
conversation reasoning, and a document whose length answers to the evidence
behind it rather than to a constant.

No network and no model. The analysis stage is exercised through a stubbed
`analysis_engine`, which is also how the fabrication gate is tested: a stub
that cites a message id which does not exist must not get that claim into the
report.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.features.reports import analysis as analysis_stage
from app.services.features.reports import composition, pipeline, quality
from app.services.features.reports.analysis import (
    Decision,
    EvidenceStatus,
    FollowUp,
    PeriodSynthesis,
    ThreadFinding,
    ThreadFindings,
    Workstream,
)
from app.services.features.reports.correspondence import (
    Correspondence,
    Direction,
    Message,
    Participant,
)
from app.services.features.reports.period import PeriodKind, range_between, to_date
from app.services.features.reports.threads import ThreadShape, take_inventory

OWNER = "owner@sunradia.com"


def _message(
    message_id: str,
    thread_id: str,
    *,
    day: int,
    direction: Direction = Direction.INBOUND,
    subject: str = "Quote for 520 Factory Lane",
    body: str = "The quote came in well over what we expected.",
    sender: str = "client@acmeproperty.com",
) -> Message:
    return Message(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        body=body,
        sender=Participant(
            address=OWNER if direction is Direction.OUTBOUND else sender
        ),
        recipients=[
            Participant(address=sender if direction is Direction.OUTBOUND else OWNER)
        ],
        cc=[],
        occurred_at=datetime(2026, 8, day, 10, 0, tzinfo=UTC),
        direction=direction,
    )


def _corpus(*messages: Message) -> Correspondence:
    return Correspondence(
        mailbox=OWNER,
        owner_name="Pat Owner",
        period=range_between(
            datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
        ),
        messages=list(messages),
    )


def _busy(threads: int = 12) -> Correspondence:
    """A corpus with enough conversations to clear the quiet-period bar."""

    messages: list[Message] = []

    for index in range(threads):
        messages.append(_message(f"m{index}a", f"t{index}", day=1 + index % 20))
        messages.append(
            _message(
                f"m{index}b",
                f"t{index}",
                day=2 + index % 20,
                direction=Direction.OUTBOUND,
            )
        )

    return _corpus(*messages)


# --- thread reconstruction ------------------------------------------------


class TestConversationsRatherThanMessages:
    def test_messages_sharing_a_thread_id_become_one_conversation(self) -> None:
        inventory = take_inventory(
            _corpus(
                _message("m1", "t1", day=1),
                _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
                _message("m3", "t1", day=3),
            )
        )

        assert len(inventory.substantive) == 1
        assert len(inventory.substantive[0].messages) == 3

    def test_a_thread_knows_whether_the_owner_replied(self) -> None:
        answered = take_inventory(
            _corpus(
                _message("m1", "t1", day=1),
                _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
            )
        ).substantive[0]
        ignored = take_inventory(
            _corpus(_message("m1", "t1", day=1), _message("m2", "t1", day=2))
        ).substantive[0]

        assert answered.owner_replied is True
        assert ignored.owner_replied is False

    def test_the_shape_says_who_spoke_last(self) -> None:
        """The distinction the report turns on, and pure arithmetic."""

        thread = take_inventory(
            _corpus(
                _message("m1", "t1", day=1),
                _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
            )
        ).substantive[0]

        assert thread.shape is ThreadShape.AWAITING_REPLY

    def test_automated_mail_is_separated_rather_than_deleted(self) -> None:
        inventory = take_inventory(
            _corpus(
                _message("m1", "t1", day=1),
                _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
                _message(
                    "n1",
                    "t2",
                    day=3,
                    sender="no-reply@jobboard.example",
                    subject="New candidates matching your search",
                ),
                _message(
                    "n2",
                    "t2",
                    day=4,
                    sender="no-reply@jobboard.example",
                    subject="New candidates matching your search",
                ),
            )
        )

        assert [t.thread_id for t in inventory.substantive] == ["t1"]
        assert [t.thread_id for t in inventory.routine] == ["t2"]
        assert inventory.automated_message_count == 2


# --- period resolution ----------------------------------------------------


class TestPeriodsIncludingPartialOnes:
    def test_month_to_date_is_a_partial_period(self) -> None:
        window = to_date(PeriodKind.MONTH, datetime(2026, 9, 13, 14, tzinfo=UTC))

        assert window.is_partial is True
        assert window.start == datetime(2026, 9, 1, tzinfo=UTC)
        assert window.end == datetime(2026, 9, 13, 14, tzinfo=UTC)

    def test_a_whole_month_expressed_as_a_range_is_not_partial(self) -> None:
        """Bounds decide this, not how the period was constructed."""

        window = range_between(
            datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
        )

        assert window.is_partial is False

    def test_september_to_date_splits_into_the_weeks_asked_for(self) -> None:
        """The worked example: 1–13 September becomes 1–7 and 8–13."""

        from app.services.features.reports.period import split_into_weeks

        window = to_date(PeriodKind.MONTH, datetime(2026, 9, 13, 12, tzinfo=UTC))
        segments = split_into_weeks(window)

        assert [segment.label for segment in segments] == [
            "1 Sep – 7 Sep 2026",
            "8 Sep – 13 Sep 2026",
        ]

    @pytest.mark.parametrize(
        ("month", "day", "expected"),
        [(2, 29, 5), (4, 30, 5), (12, 31, 5)],
    )
    def test_the_split_is_arithmetic_not_a_calendar_table(
        self, month: int, day: int, expected: int
    ) -> None:
        """Nothing is hardcoded for any month, including a leap February."""

        from app.services.features.reports.period import month_of, split_into_weeks

        window = month_of(datetime(2024, month, day, tzinfo=UTC))

        assert len(split_into_weeks(window)) == expected


# --- the deterministic floor ----------------------------------------------


class TestWithoutAModel:
    def test_a_report_is_still_produced(self) -> None:
        result = pipeline.generate(_busy(), use_model=False)

        assert result.document.markdown.startswith("# Activity Report — Pat Owner")

    def test_it_is_not_five_lines(self) -> None:
        """The failure this pipeline exists to prevent, asserted directly."""

        result = pipeline.generate(_busy(), use_model=False)

        assert result.document.word_count > 400
        assert result.document.markdown.count("\n## ") >= 8

    def test_it_names_conversations_rather_than_counting_them(self) -> None:
        result = pipeline.generate(_busy(), use_model=False)

        assert result.document.thread_count == 12

    def test_the_header_says_no_narrative_was_available(self) -> None:
        """A structural report must not read as though it were analysed."""

        result = pipeline.generate(_busy(), use_model=False)

        assert "structural analysis only" in result.document.markdown

    def test_the_quality_gate_does_not_demand_workstreams_of_it(self) -> None:
        result = pipeline.generate(_busy(), use_model=False)

        assert result.verdict.ok is True


# --- the narrative path, with a stubbed model -----------------------------


class _Stub:
    """Stands in for `analysis_engine.run`, recording what it was asked."""

    def __init__(self, *, findings=None, synthesis=None, fail: bool = False) -> None:
        self._findings = findings
        self._synthesis = synthesis
        self._fail = fail
        self.calls: list[str] = []

    def run(self, prompt_name, response_model, **variables):
        self.calls.append(prompt_name)

        if self._fail:
            raise RuntimeError("provider unavailable")

        if prompt_name == "report_thread_analysis":
            return self._findings or ThreadFindings()

        if prompt_name == "report_period_synthesis":
            return self._synthesis or PeriodSynthesis()

        return response_model()


def _finding(thread_id: str, evidence: list[str]) -> ThreadFinding:
    return ThreadFinding(
        thread_id=thread_id,
        topic="Factory Lane quotation",
        category="property_strategic",
        summary="A quote arrived over budget and two further quotes were sought.",
        owner_actions=["Requested two comparison quotes"],
        other_actions=["Submitted the initial quote"],
        outcome=None,
        status=EvidenceStatus.AWAITING_EXTERNAL_RESPONSE,
        significance="Directly affects the refurbishment budget.",
        evidence=evidence,
    )


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch):
    def _install(stub: _Stub) -> _Stub:
        monkeypatch.setattr(analysis_stage, "analysis_engine", stub)
        return stub

    return _install


class TestTheNarrativePath:
    def test_workstreams_reach_the_document(self, stubbed) -> None:
        corpus = _corpus(
            _message("m1", "t1", day=1),
            _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
        )
        stubbed(
            _Stub(
                findings=ThreadFindings(threads=[_finding("t1", ["m1", "m2"])]),
                synthesis=PeriodSynthesis(
                    workstreams=[
                        Workstream(
                            name="Factory Lane refurbishment",
                            purpose="Bring the site up to specification.",
                            activity="Quotes were sought and compared.",
                            evidence=["m1"],
                        )
                    ],
                    executive_summary=["Property refurbishment quoting progressed."],
                ),
            )
        )

        result = pipeline.generate(corpus, use_model=True)

        assert result.document.workstream_count == 1
        assert "Factory Lane refurbishment" in result.document.markdown
        assert "Property refurbishment quoting progressed." in result.document.markdown

    def test_a_claim_citing_a_message_that_does_not_exist_is_dropped(
        self, stubbed
    ) -> None:
        """The fabrication gate, and the reason this design is safe to print.

        The stub returns a workstream and a decision whose evidence names a
        message id that is not in the period. Both must be gone from the
        document — not flagged, not footnoted, gone.
        """

        corpus = _corpus(
            _message("m1", "t1", day=1),
            _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
        )
        stubbed(
            _Stub(
                findings=ThreadFindings(threads=[_finding("t1", ["m1"])]),
                synthesis=PeriodSynthesis(
                    workstreams=[
                        Workstream(name="Invented programme", evidence=["ghost-id"])
                    ],
                    decisions=[
                        Decision(
                            decision="Board approved the budget", evidence=["nope"]
                        )
                    ],
                    follow_ups=[
                        FollowUp(item="Chase the imaginary vendor", evidence=["nope"])
                    ],
                ),
            )
        )

        result = pipeline.generate(corpus, use_model=True)

        assert "Invented programme" not in result.document.markdown
        assert "Board approved the budget" not in result.document.markdown
        assert "Chase the imaginary vendor" not in result.document.markdown
        assert result.document.workstream_count == 0

    def test_a_finding_for_a_thread_that_does_not_exist_is_dropped(
        self, stubbed
    ) -> None:
        corpus = _corpus(
            _message("m1", "t1", day=1),
            _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
        )
        stubbed(_Stub(findings=ThreadFindings(threads=[_finding("t-ghost", ["m1"])])))

        result = pipeline.generate(corpus, use_model=True)

        assert "Factory Lane quotation" not in result.document.markdown

    def test_evidence_ids_are_preserved_in_the_document(self, stubbed) -> None:
        corpus = _corpus(
            _message("m1", "t1", day=1),
            _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
        )
        stubbed(_Stub(findings=ThreadFindings(threads=[_finding("t1", ["m1", "m2"])])))

        result = pipeline.generate(corpus, use_model=True)

        assert "Evidence:" in result.document.markdown

    def test_a_model_failure_falls_back_rather_than_raising(self, stubbed) -> None:
        """The endpoint must not break because a provider is down."""

        stubbed(_Stub(fail=True))

        result = pipeline.generate(_busy(), use_model=True)

        assert result.document.word_count > 400
        assert result.analysis.generated is False

    def test_a_long_period_is_analysed_week_by_week(self, stubbed) -> None:
        stub = stubbed(
            _Stub(findings=ThreadFindings(threads=[_finding("t0", ["m0a"])]))
        )

        result = pipeline.generate(_busy(), use_model=True)

        assert len(result.weekly) == 5
        assert "report_cross_period" in stub.calls
        assert "Weekly Progression" in result.document.markdown

    def test_a_single_week_is_not_split_into_itself(self, stubbed) -> None:
        stubbed(_Stub())
        window = range_between(
            datetime(2026, 8, 3, tzinfo=UTC), datetime(2026, 8, 10, tzinfo=UTC)
        )

        result = pipeline.generate(_busy(), period=window, use_model=True)

        assert result.weekly == []
        assert "Weekly Progression" not in result.document.markdown


# --- quality control ------------------------------------------------------


class TestQualityIsJudgedAgainstTheEvidence:
    def test_a_five_line_report_over_a_busy_period_fails(self) -> None:
        """The exact regression: hundreds of messages, a handful of bullets."""

        inventory = take_inventory(_busy(40))
        document = composition.ReportDocument(
            title="t",
            markdown=(
                "# Activity Report\n\n- 200 emails received\n- 0 sent\n- 2 tasks open\n"
            ),
            section_titles=list(quality.REQUIRED_SECTIONS),
        )

        verdict = quality.assess(document, inventory)

        assert verdict.failed is True
        assert any("words" in failure for failure in verdict.failures)

    def test_a_short_report_over_a_quiet_period_passes(self) -> None:
        """A quiet week is not a defect, and must not be padded into one."""

        inventory = take_inventory(
            _corpus(
                _message("m1", "t1", day=1),
                _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
            )
        )

        assert quality.expected_words(inventory) == 220

    def test_the_expected_length_scales_with_the_evidence(self) -> None:
        assert quality.expected_words(
            take_inventory(_busy(40))
        ) > quality.expected_words(take_inventory(_busy(8)))

    def test_a_report_made_mostly_of_numbers_fails(self) -> None:
        inventory = take_inventory(_busy(40))
        document = composition.ReportDocument(
            title="t",
            markdown=" ".join(f"{n} messages" for n in range(900)),
            section_titles=list(quality.REQUIRED_SECTIONS),
        )

        verdict = quality.assess(document, inventory)

        assert any("numeric" in failure for failure in verdict.failures)

    def test_a_missing_section_fails(self) -> None:
        document = composition.ReportDocument(
            title="t", markdown="x " * 900, section_titles=["Executive Summary"]
        )

        verdict = quality.assess(document, take_inventory(_busy(40)))

        assert any("Missing required section" in f for f in verdict.failures)

    def test_the_gate_expands_a_thin_report_rather_than_failing_it(
        self, stubbed
    ) -> None:
        """A busy period whose first pass was short is re-rendered fuller."""

        stubbed(_Stub(fail=True))

        result = pipeline.generate(_busy(40), use_model=True)

        assert result.verdict.ok is True
        assert result.document.word_count >= result.verdict.expected_words


# --- required structure ---------------------------------------------------


class TestTheDocumentShape:
    def test_every_required_section_is_present(self) -> None:
        result = pipeline.generate(_busy(), use_model=False)

        for title in quality.REQUIRED_SECTIONS:
            assert title in result.document.section_titles

    def test_an_empty_section_explains_itself_rather_than_vanishing(self) -> None:
        """A missing heading reads as a broken report; a note reads as a finding."""

        result = pipeline.generate(_busy(), use_model=False)

        assert "Deadlines and Important Dates" in result.document.markdown
        assert "No dates were explicitly stated" in result.document.markdown

    def test_a_partial_period_is_labelled_as_partial(self) -> None:
        window = range_between(
            datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 13, tzinfo=UTC)
        )

        result = pipeline.generate(_busy(), period=window, use_model=False)

        assert "partial" in result.document.markdown

    def test_a_whole_month_is_not_labelled_partial(self) -> None:
        result = pipeline.generate(_busy(), use_model=False)

        assert "Report type:** Monthly" in result.document.markdown

    def test_calendar_and_task_limitations_are_carried_into_the_header(self) -> None:
        corpus = _corpus(
            _message("m1", "t1", day=1),
            _message("m2", "t1", day=2, direction=Direction.OUTBOUND),
        )
        corpus = Correspondence(
            mailbox=corpus.mailbox,
            owner_name=corpus.owner_name,
            period=corpus.period,
            messages=corpus.messages,
            limitations=["Calendar not readable: Graph denied the request (403)."],
        )

        result = pipeline.generate(corpus, use_model=False)

        assert "Calendar not readable" in result.document.markdown
