import pytest

import app.prompts.system as system_prompts
from app.prompts import register_default_prompts
from app.prompts.base import PromptTemplate
from app.prompts.builder import PromptBuilder
from app.prompts.registry import PromptRegistry

# Sorted, because `PromptRegistry.list()` sorts.
EXPECTED_PROMPT_NAMES = [
    "assistant",
    "email_compose",
    "email_subject",
    "email_thread_summary",
    "email_transform",
    "email_triage",
    "rag_answer",
]


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


def test_every_email_prompt_renders_with_its_variables() -> None:
    """Each template's variables are supplied by exactly one service.

    Rendering puts *both* prompts through `str.format`, so a stray brace in a
    system prompt — a JSON example, most likely — breaks the template at call
    time rather than at import. This catches that here instead of in a 502.
    """

    register_default_prompts()

    cases = {
        "email_compose": {
            "persona": "P",
            "knowledge": "K",
            "source_email": "S",
            "recipients": "R",
            "tone": "T",
            "instruction": "I",
        },
        "email_transform": {
            "persona": "P",
            "knowledge": "K",
            "subject": "S",
            "body": "B",
            "instruction": "I",
        },
        "email_subject": {"persona": "P", "body": "B", "instruction": "I"},
        "email_triage": {"persona": "P", "message": "M"},
        "email_thread_summary": {"persona": "P", "thread": "T"},
    }

    for name, variables in cases.items():
        messages = PromptBuilder.build(PromptRegistry.get(name), **variables)

        assert len(messages) == 2, name
        # The JSON shape survives `.format` — doubled braces became single ones.
        assert '{"' in messages[0]["content"], name


def test_the_writing_prompts_forbid_inventing_company_facts() -> None:
    """The grounding rule is the feature. If it is softened, the agent starts
    describing services SunRadia may not offer, in SunRadia's own voice."""

    for prompt in (
        system_prompts.EMAIL_COMPOSE_PROMPT,
        system_prompts.EMAIL_TRANSFORM_PROMPT,
    ):
        system = prompt.system_prompt.lower()

        assert "never state a fact about the sender's company" in system
        assert "do not substitute plausible ones" in system
        assert "never invent a name, a price, a deadline" in system


def test_the_compose_prompt_says_it_never_sends() -> None:
    """A model that believes it can send is one prompt injection away from
    trying. The refusal is stated in the prompt as well as in the code."""

    assert "never send anything" in (
        system_prompts.EMAIL_COMPOSE_PROMPT.system_prompt.lower()
    )


def test_the_triage_prompt_refuses_to_invent_a_deadline() -> None:
    system = system_prompts.EMAIL_TRIAGE_PROMPT.system_prompt.lower()

    assert "never choose a deadline yourself" in system
