"""A stand-in for the chat-completion provider.

Chat tests must never reach a real model: it would be slow, paid for, and
non-deterministic in exactly the dimension the assertions care about.
"""

from collections.abc import Callable, Iterator

import pytest

from app.prompts import register_default_prompts
from app.prompts.builder import ChatMessage
from app.services.llm.llm_service import llm_service


@pytest.fixture
def fake_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], list[list[ChatMessage]]]]:
    """Pin the model's reply, and record the messages it was sent.

    Returns an installer; calling it yields the list that accumulates one
    entry per completion, so a test can assert both what the model answered
    and what it was actually shown.
    """

    calls: list[list[ChatMessage]] = []

    def install(reply: str) -> list[list[ChatMessage]]:
        def complete(messages: list[ChatMessage]) -> str:
            calls.append(messages)
            return reply

        monkeypatch.setattr(llm_service, "complete", complete)
        return calls

    yield install


@pytest.fixture(autouse=True)
def registered_prompts(clear_prompt_registry: None) -> None:
    """Re-register the built-in prompts after the root fixture clears them.

    `register_default_prompts()` runs once at application import, but the root
    conftest empties the registry around every test — so anything that renders
    a registered prompt needs it put back, explicitly after the clear.
    """

    register_default_prompts()
