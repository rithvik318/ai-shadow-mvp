from collections.abc import Iterator

import pytest

from app.prompts.registry import PromptRegistry


@pytest.fixture(autouse=True)
def clear_prompt_registry() -> Iterator[None]:
    """Clear the prompt registry before and after every test."""

    PromptRegistry.clear()
    yield
    PromptRegistry.clear()
