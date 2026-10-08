from typing import Any

import pytest
from pydantic import BaseModel

from app.core.exceptions import AnalysisValidationError, PromptNotFoundError
from app.prompts.base import PromptTemplate
from app.prompts.registry import PromptRegistry
from app.services.engines.analysis.analysis_engine import analysis_engine
from app.services.llm.llm_service import llm_service


class SampleAnalysisResult(BaseModel):
    """A minimal, domain-agnostic response model used only by these tests."""

    value: str
    count: int


@pytest.fixture
def sample_template() -> PromptTemplate:
    template = PromptTemplate(
        name="sample_analysis",
        system_prompt="Return JSON with 'value' and 'count' about {topic}.",
        user_prompt="Topic: {topic}",
    )
    PromptRegistry.register(template)
    return template


def test_run_returns_validated_response_model_on_success(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """Well-formed JSON is parsed into the requested response model."""

    monkeypatch.setattr(
        llm_service, "complete", lambda messages: '{"value": "ok", "count": 3}'
    )

    result = analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")

    assert result == SampleAnalysisResult(value="ok", count=3)


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"value": "ok", "count": 3}\n```',
        '```\n{"value": "ok", "count": 3}\n```',
        '  ```JSON\n{"value": "ok", "count": 3}\n```  ',
    ],
)
def test_run_tolerates_markdown_fenced_json(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate, raw: str
) -> None:
    """Models routinely fence JSON despite instructions not to; that should
    not fail the request."""

    monkeypatch.setattr(llm_service, "complete", lambda messages: raw)

    result = analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")

    assert result.value == "ok"


def test_run_passes_template_variables_into_the_rendered_prompt(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """Variables supplied to run() reach the rendered messages."""

    captured: list[Any] = []

    def fake_complete(messages: Any) -> str:
        captured.extend(messages)
        return '{"value": "ok", "count": 1}'

    monkeypatch.setattr(llm_service, "complete", fake_complete)

    analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="pytest")

    user_message = next(m for m in captured if m["role"] == "user")
    assert "pytest" in user_message["content"]


def test_run_raises_analysis_validation_error_for_invalid_json(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    monkeypatch.setattr(llm_service, "complete", lambda messages: "not valid json")

    with pytest.raises(AnalysisValidationError):
        analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")


def test_run_raises_analysis_validation_error_for_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    monkeypatch.setattr(
        llm_service, "complete", lambda messages: '{"unexpected": "field"}'
    )

    with pytest.raises(AnalysisValidationError):
        analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")


def test_run_raises_prompt_not_found_error_for_unknown_prompt_name() -> None:
    with pytest.raises(PromptNotFoundError):
        analysis_engine.run("does_not_exist", SampleAnalysisResult, topic="t")


# --- what a real model actually returns ----------------------------------
#
# Every one of these was a lost batch of conversations in the report pipeline,
# logged as `report_thread_batch_failed` with no indication of which it was.


def test_an_empty_response_says_so_rather_than_reporting_bad_json(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """A successful call whose content is empty is its own diagnosis.

    Reasoning models routinely put their answer in a channel the Chat
    Completions `content` field does not carry, and "not valid JSON" sends
    whoever reads the log looking for a malformed object that was never there.
    """

    monkeypatch.setattr(llm_service, "complete", lambda messages: "   ")

    with pytest.raises(AnalysisValidationError, match="empty response"):
        analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")


def test_json_wrapped_in_prose_is_still_read(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """A preamble is a formatting habit, not a refusal.

    Losing a batch of twelve conversations to the words "Here is the analysis"
    is a worse report for no reason.
    """

    monkeypatch.setattr(
        llm_service,
        "complete",
        lambda messages: (
            'Here is the analysis:\n{"value": "ok", "count": 3}\nHope that helps.'
        ),
    )

    result = analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")

    assert result == SampleAnalysisResult(value="ok", count=3)


def test_a_failure_message_carries_no_model_output(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """The length, never the content.

    A model's reply quotes the correspondence it was shown, and these messages
    are logged.
    """

    secret = "Confidential: the Factory Lane quotation is 84,000"
    monkeypatch.setattr(llm_service, "complete", lambda messages: secret)

    with pytest.raises(AnalysisValidationError) as raised:
        analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")

    assert secret not in str(raised.value)
    assert str(len(secret)) in str(raised.value)


# --- the malformed JSON a live report batch returned ----------------------


def test_a_stray_backslash_is_kept_literally_rather_than_losing_the_batch(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """`JSONDecodeError: Invalid \\escape` from a live report batch.

    The model quoted mail containing a Windows path. The text is kept exactly
    as written; a valid escape beside it is not disturbed.
    """

    raw = r'{"value": "Saved to D:\Shared\Q3 \"final\" \\ done", "count": 2}'

    monkeypatch.setattr(llm_service, "complete", lambda messages: raw)

    result = analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")

    assert result.value == 'Saved to D:\\Shared\\Q3 "final" \\ done'
    assert result.count == 2


def test_a_missing_comma_is_refused_not_guessed(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """`Expecting ',' delimiter` is not repaired: there is no safe guess."""

    monkeypatch.setattr(
        llm_service, "complete", lambda messages: '{"value": "ok" "count": 3}'
    )

    with pytest.raises(AnalysisValidationError, match="not valid JSON"):
        analysis_engine.run("sample_analysis", SampleAnalysisResult, topic="t")


def test_structured_output_sends_the_response_model_as_a_json_schema(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    captured: dict[str, Any] = {}

    def fake_complete(messages: Any, response_format: Any = None) -> str:
        captured["response_format"] = response_format
        return '{"value": "ok", "count": 1}'

    monkeypatch.setattr(llm_service, "complete", fake_complete)

    analysis_engine.run(
        "sample_analysis", SampleAnalysisResult, structured_output=True, topic="t"
    )

    sent = captured["response_format"]
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["name"] == "SampleAnalysisResult"
    assert sent["json_schema"]["schema"] == SampleAnalysisResult.model_json_schema()


def test_without_structured_output_the_call_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, sample_template: PromptTemplate
) -> None:
    """Other callers of the engine send exactly what they sent before."""

    monkeypatch.setattr(
        llm_service, "complete", lambda messages: '{"value": "ok", "count": 1}'
    )

    assert analysis_engine.run(
        "sample_analysis", SampleAnalysisResult, topic="t"
    ) == SampleAnalysisResult(value="ok", count=1)
