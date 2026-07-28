import pytest

from app.core.exceptions import PromptTemplateError
from app.prompts.base import PromptTemplate
from app.prompts.builder import PromptBuilder


def test_build_returns_ordered_system_and_user_messages() -> None:
    """PromptBuilder returns system and user messages in order."""

    template = PromptTemplate("t", "You are helpful.", "Help with this.")

    assert PromptBuilder.build(template) == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Help with this."},
    ]


def test_build_omits_empty_rendered_system_prompt() -> None:
    """A system prompt rendering to empty is dropped."""

    template = PromptTemplate("t", "{system_content}", "Help with this.")

    assert PromptBuilder.build(template, system_content="") == [
        {"role": "user", "content": "Help with this."},
    ]


def test_build_omits_empty_rendered_user_prompt() -> None:
    """A user prompt rendering to empty is dropped."""

    template = PromptTemplate("t", "You are helpful.", "{user_content}")

    assert PromptBuilder.build(template, user_content="") == [
        {"role": "system", "content": "You are helpful."},
    ]


def test_build_renders_template_variables_in_both_messages() -> None:
    """Variables reach both rendered messages."""

    template = PromptTemplate(
        "t", "You are an expert in {topic}.", "Explain {topic} to {audience}."
    )

    assert PromptBuilder.build(template, topic="Python", audience="beginners") == [
        {"role": "system", "content": "You are an expert in Python."},
        {"role": "user", "content": "Explain Python to beginners."},
    ]


def test_build_propagates_missing_template_variable_error() -> None:
    """Rendering errors are not swallowed."""

    template = PromptTemplate("t", "Helping with {topic}.", "Explain {topic}.")

    with pytest.raises(PromptTemplateError):
        PromptBuilder.build(template)
