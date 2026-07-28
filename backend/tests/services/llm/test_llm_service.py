from types import SimpleNamespace

import pytest

from app.services.llm import client as client_module
from app.services.llm.llm_service import llm_service


@pytest.fixture(autouse=True)
def _reset_client() -> None:
    """The client is a process-wide singleton; keep tests independent."""

    client_module.reset_llm_client()


def test_complete_sends_messages_and_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """complete() forwards messages to the provider and returns the text,
    with no provider-specific branching of its own."""

    captured: dict[str, object] = {}

    def fake_create(*, model: str, messages: list) -> SimpleNamespace:
        captured["model"] = model
        captured["messages"] = messages
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="the reply"))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(client_module, "get_llm_client", lambda: fake_client)
    monkeypatch.setattr(
        "app.services.llm.llm_service.get_llm_client", lambda: fake_client
    )

    messages = [{"role": "user", "content": "Hello"}]
    result = llm_service.complete(messages)

    assert result == "the reply"
    assert captured["messages"] == messages


def test_complete_returns_empty_string_when_content_is_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A null content field yields an empty string rather than None."""

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
                )
            )
        )
    )
    monkeypatch.setattr(
        "app.services.llm.llm_service.get_llm_client", lambda: fake_client
    )

    assert llm_service.complete([{"role": "user", "content": "Hi"}]) == ""


def test_get_llm_client_rejects_an_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider selection is the only place that knows provider names."""

    monkeypatch.setattr(client_module.settings, "LLM_PROVIDER", "not-a-provider")

    with pytest.raises(ValueError):
        client_module.get_llm_client()
