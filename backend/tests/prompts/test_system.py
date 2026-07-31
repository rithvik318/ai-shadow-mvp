import pytest

import app.prompts.system as system_prompts
from app.prompts import register_default_prompts
from app.prompts.base import PromptTemplate
from app.prompts.builder import PromptBuilder
from app.prompts.registry import PromptRegistry

EXPECTED_PROMPT_NAMES = ["assistant", "rag_answer"]


@pytest.mark.parametrize("export_name", system_prompts.__all__)
def test_exported_builtin_prompts_are_valid_templates(export_name: str) -> None:
    """Every export is a template with non-empty content."""

    prompt = getattr(system_prompts, export_name)

    assert isinstance(prompt, PromptTemplate)
    assert prompt.name.strip()
    assert prompt.system_prompt.strip()
    assert prompt.user_prompt.strip()


def test_builtin_prompt_names_are_unique() -> None:
    names = [
        getattr(system_prompts, export_name).name
        for export_name in system_prompts.__all__
    ]

    assert len(names) == len(set(names))


def test_register_default_prompts_registers_every_builtin() -> None:
    """Only prompts with a caller are shipped; see docs/DECISIONS.md."""

    register_default_prompts()

    assert PromptRegistry.list() == EXPECTED_PROMPT_NAMES


def test_register_default_prompts_is_idempotent() -> None:
    """Repeated registration does not raise or duplicate."""

    register_default_prompts()
    register_default_prompts()

    assert PromptRegistry.list() == EXPECTED_PROMPT_NAMES


def test_rag_answer_prompt_renders_context_and_question() -> None:
    """The retrieval prompt accepts retrieved context and a question."""

    register_default_prompts()

    messages = PromptBuilder.build(
        PromptRegistry.get("rag_answer"),
        context="Knowledge Base",
        question="What should I do?",
    )

    assert len(messages) == 2
    assert "Knowledge Base" in messages[1]["content"]
    assert "What should I do?" in messages[1]["content"]


def test_rag_answer_prompt_forbids_answering_outside_the_context() -> None:
    """The grounding rules are the feature, not decoration — if they are
    softened, the model starts answering from prior knowledge and the
    citations stop meaning anything."""

    # Read from the module, not the registry: this asserts what the template
    # says, which is true whether or not anything has registered it.
    system = system_prompts.RAG_ANSWER_PROMPT.system_prompt.lower()

    assert "only source of truth" in system
    assert "never introduce" in system
    assert "say so plainly" in system


def test_assistant_prompt_renders_input() -> None:
    register_default_prompts()

    messages = PromptBuilder.build(
        PromptRegistry.get("assistant"), input="Help me plan my day."
    )

    assert messages[0]["role"] == "system"
    assert "Help me plan my day." in messages[1]["content"]
