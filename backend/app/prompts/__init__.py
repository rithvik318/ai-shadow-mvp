from app.prompts.registry import PromptRegistry
from app.prompts.system import AI_SHADOW_PROMPT, ASSISTANT_PROMPT


def register_default_prompts() -> None:
    """Register built-in prompt templates if they are not already registered."""

    default_prompts = (ASSISTANT_PROMPT, AI_SHADOW_PROMPT)

    for prompt in default_prompts:
        if not PromptRegistry.exists(prompt.name):
            PromptRegistry.register(prompt)


__all__ = ["register_default_prompts"]
