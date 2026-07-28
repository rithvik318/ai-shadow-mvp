import dataclasses

import pytest

from app.core.exceptions import PromptTemplateError
from app.prompts.base import PromptTemplate


def test_render_returns_rendered_system_and_user_prompts() -> None:
    """Prompt templates render both prompts with supplied variables."""

    template = PromptTemplate(
        name="test_prompt",
        system_prompt="You are helping with {topic}.",
        user_prompt="Explain {topic} to {audience}.",
    )

    rendered = template.render(topic="Python", audience="beginners")

    assert rendered == {
        "system": "You are helping with Python.",
        "user": "Explain Python to beginners.",
    }


def test_render_raises_error_for_missing_template_variable() -> None:
    """Missing template variables raise PromptTemplateError."""

    template = PromptTemplate("t", "Helping with {topic}.", "Explain {topic}.")

    with pytest.raises(PromptTemplateError):
        template.render()


def test_render_raises_error_for_invalid_template_formatting() -> None:
    """Malformed format strings raise PromptTemplateError."""

    template = PromptTemplate("t", "Helping with {topic.", "Explain it.")

    with pytest.raises(PromptTemplateError):
        template.render(topic="Python")


@pytest.mark.parametrize(
    "name, system_prompt, user_prompt",
    [
        ("   ", "You are helpful.", "Help me."),
        ("t", "   ", "Help me."),
        ("t", "You are helpful.", "   "),
    ],
)
def test_initialization_rejects_empty_fields(
    name: str, system_prompt: str, user_prompt: str
) -> None:
    """Names and prompt bodies cannot be blank."""

    with pytest.raises(ValueError):
        PromptTemplate(name, system_prompt, user_prompt)


def test_templates_are_immutable() -> None:
    """Templates are frozen value objects."""

    template = PromptTemplate("t", "System.", "User.")

    with pytest.raises(dataclasses.FrozenInstanceError):
        template.name = "other"  # type: ignore[misc]
