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
