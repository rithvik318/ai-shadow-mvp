import pytest

from app.core.exceptions import DuplicatePromptError, PromptNotFoundError
from app.prompts.base import PromptTemplate
from app.prompts.registry import PromptRegistry


def test_register_adds_prompt_template_to_registry() -> None:
    """Templates can be registered and found."""

    template = PromptTemplate("test_prompt", "System prompt.", "User prompt.")

    PromptRegistry.register(template)

    assert PromptRegistry.exists("test_prompt") is True


def test_get_returns_the_registered_instance() -> None:
    """Lookup returns the same object that was registered."""

    template = PromptTemplate("test_prompt", "System prompt.", "User prompt.")
    PromptRegistry.register(template)

    assert PromptRegistry.get("test_prompt") is template


def test_exists_returns_false_for_unknown_prompt() -> None:
    assert PromptRegistry.exists("unknown_prompt") is False


def test_list_returns_registered_names_sorted() -> None:
    PromptRegistry.register(PromptTemplate("second", "S.", "U."))
    PromptRegistry.register(PromptTemplate("first", "S.", "U."))

    assert PromptRegistry.list() == ["first", "second"]


def test_register_raises_error_for_duplicate_prompt_name() -> None:
    """Duplicate registration fails loudly rather than overwriting."""

    template = PromptTemplate("test_prompt", "System prompt.", "User prompt.")
    PromptRegistry.register(template)

    with pytest.raises(DuplicatePromptError):
        PromptRegistry.register(template)


def test_get_raises_error_for_unknown_prompt() -> None:
    with pytest.raises(PromptNotFoundError):
        PromptRegistry.get("unknown_prompt")


def test_clear_removes_all_registered_prompt_templates() -> None:
    PromptRegistry.register(PromptTemplate("first", "S.", "U."))

    PromptRegistry.clear()

    assert PromptRegistry.list() == []
