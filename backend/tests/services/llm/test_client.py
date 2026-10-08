"""`llm_configured`: whether a model can be attempted at all.

The question exists because a caller that wants a narrative report and a caller
that wants a deterministic one differ by exactly this, and the alternative —
each call site reading credentials itself — is how a reference to a setting
that does not exist (`settings.LLM_API_KEY`) reached the activity report
endpoint and answered HTTP 500 to every request with a mailbox connected.

`conftest.py` resets every setting to its in-code default around each test, so
nothing here can be answered by a populated `.env`.
"""

import pytest

from app.config.settings import settings
from app.services.llm.client import get_client, llm_configured, reset_llm_client


class TestLLMConfigured:
    def test_no_credentials_means_no_model(self) -> None:
        assert llm_configured() is False

    def test_the_provider_in_use_is_the_one_asked_about(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unrelated key is not a configured model.

        A deployment pointed at OpenRouter with only an OpenAI key present has
        no way to call a model, and reporting it as ready would produce a
        report that fails one LLM call at a time rather than falling back once,
        cleanly, and saying so in its header.
        """

        monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)

        assert llm_configured() is False

        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-openrouter")

        assert llm_configured() is True

    def test_openai_is_read_from_its_own_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "LLM_PROVIDER", "OpenAI")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")

        assert llm_configured() is True

    def test_an_unknown_provider_is_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No exception, and no false claim of readiness.

        A misspelt `LLM_PROVIDER` should degrade the report to its structural
        form, not take the endpoint down.
        """

        monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropicc")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")

        assert llm_configured() is False


class TestTheBoundsOnOneCall:
    """The SDK's defaults are wrong for a request somebody is waiting on.

    `openai` defaults to a 600-second read timeout and two automatic retries.
    The report pipeline makes a dozen calls in sequence, so those defaults were
    inherited as a multiplier: measured against a stub provider that answered
    500, 18 logical calls became 54 HTTP attempts, each entitled to ten
    minutes.
    """

    def test_a_client_carries_the_configured_timeout_and_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 12.5)
        monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 0)
        reset_llm_client()

        client = get_client("openai")

        assert client.timeout == 12.5
        assert client.max_retries == 0

        reset_llm_client()

    def test_the_openrouter_client_is_bounded_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 9.0)
        monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
        reset_llm_client()

        client = get_client("openrouter")

        assert client.timeout == 9.0
        assert client.max_retries == 1

        reset_llm_client()

    def test_the_defaults_are_bounded_rather_than_the_sdks(self) -> None:
        """A deployment that configures nothing still gets a bounded call."""

        assert settings.LLM_TIMEOUT_SECONDS <= 120
        assert settings.LLM_MAX_RETRIES <= 1
        assert settings.REPORT_ANALYSIS_BUDGET_SECONDS <= 300
