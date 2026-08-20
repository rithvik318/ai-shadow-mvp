from app.prompts.registry import PromptRegistry
from app.prompts.system import (
    ASSISTANT_PROMPT,
    EMAIL_COMPOSE_PROMPT,
    EMAIL_SUBJECT_PROMPT,
    EMAIL_THREAD_SUMMARY_PROMPT,
    EMAIL_TRANSFORM_PROMPT,
    EMAIL_TRIAGE_PROMPT,
    RAG_ANSWER_PROMPT,
)


def register_default_prompts() -> None:
    """Register built-in prompt templates if they are not already registered."""

    default_prompts = (
        ASSISTANT_PROMPT,
        RAG_ANSWER_PROMPT,
        EMAIL_COMPOSE_PROMPT,
        EMAIL_TRANSFORM_PROMPT,
        EMAIL_SUBJECT_PROMPT,
        EMAIL_TRIAGE_PROMPT,
        EMAIL_THREAD_SUMMARY_PROMPT,
    )

    for prompt in default_prompts:
        if not PromptRegistry.exists(prompt.name):
            PromptRegistry.register(prompt)


__all__ = ["register_default_prompts"]
