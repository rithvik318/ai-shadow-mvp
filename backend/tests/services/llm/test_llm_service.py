from types import SimpleNamespace

import pytest

from app.services.llm import client as client_module
from app.services.llm.llm_service import MAX_OUTPUT_TOKENS, llm_service


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

    def fake_create(*, model: str, messages: list, max_tokens: int) -> SimpleNamespace:
        captured["model"] = model
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
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
    # Bounds OpenRouter's per-request output reservation (402 in-flight budget).
    assert captured["max_tokens"] == MAX_OUTPUT_TOKENS == 4096


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


def _client_recording(calls: list[dict], *, reject_format: bool) -> SimpleNamespace:
    import httpx
    from openai import BadRequestError

    def create(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)

        if reject_format and "response_format" in kwargs:
            raise BadRequestError(
                "unsupported",
                response=httpx.Response(
                    400, request=httpx.Request("POST", "https://x.test")
                ),
                body=None,
            )

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def test_structured_output_is_requested_with_openrouter_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(client_module.settings, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(
        "app.services.llm.llm_service.get_llm_client",
        lambda: _client_recording(calls, reject_format=False),
    )
    schema = {"type": "json_schema", "json_schema": {"name": "X", "schema": {}}}

    assert llm_service.complete([{"role": "user", "content": "Hi"}], schema) == "{}"

    assert calls[0]["response_format"] == schema
    assert calls[0]["extra_body"] == {"provider": {"require_parameters": True}}
    assert calls[0]["max_tokens"] == 4096


def test_a_provider_that_refuses_structured_output_gets_the_plain_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for structure must never fail a call that would have succeeded."""

    calls: list[dict] = []
    monkeypatch.setattr(
        "app.services.llm.llm_service.get_llm_client",
        lambda: _client_recording(calls, reject_format=True),
    )

    result = llm_service.complete(
        [{"role": "user", "content": "Hi"}],
        {"type": "json_schema", "json_schema": {"name": "X", "schema": {}}},
    )

    assert result == "{}"
    assert len(calls) == 2
    assert "response_format" not in calls[1]
    assert calls[0]["max_tokens"] == 4096
    assert calls[1]["max_tokens"] == 4096
