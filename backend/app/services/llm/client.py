from openai import OpenAI

from app.config.settings import settings

_client: OpenAI | None = None


def get_llm_client() -> OpenAI:
    """Return the process-wide LLM client, constructing it on first use.

    Construction is deferred rather than done at import time so that importing
    any module in this package does not require live credentials. The reference
    repository built the client eagerly, which made the whole application — and
    therefore its test suite — unimportable without a populated `.env`.
    """

    global _client

    if _client is not None:
        return _client

    provider = settings.LLM_PROVIDER.lower()

    if provider == "openai":
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    elif provider == "openrouter":
        _client = OpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    return _client


def reset_llm_client() -> None:
    """Discard the cached client. Intended for tests that swap providers."""

    global _client
    _client = None
