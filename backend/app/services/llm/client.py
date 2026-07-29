from openai import OpenAI

from app.config.settings import settings

# Cached per provider name rather than as a single client, because completions
# and embeddings may deliberately point at different providers — see
# `get_embedding_client()`.
_clients: dict[str, OpenAI] = {}

_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
}


def _build_client(provider: str) -> OpenAI:
    if provider == "openai":
        return OpenAI(api_key=settings.OPENAI_API_KEY)

    if provider == "openrouter":
        return OpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=_BASE_URLS["openrouter"],
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")


def get_client(provider: str) -> OpenAI:
    """Return the cached client for a provider, constructing it on first use.

    Construction is deferred rather than done at import time so that importing
    any module in this package does not require live credentials. The reference
    repository built the client eagerly, which made the whole application — and
    therefore its test suite — unimportable without a populated `.env`.
    """

    normalized = provider.lower()

    if normalized not in _clients:
        _clients[normalized] = _build_client(normalized)

    return _clients[normalized]


def get_llm_client() -> OpenAI:
    """Return the client for chat completions, per `LLM_PROVIDER`."""

    return get_client(settings.LLM_PROVIDER)


def get_embedding_client() -> OpenAI:
    """Return the client for embeddings.

    Falls back to `LLM_PROVIDER` when `EMBEDDING_PROVIDER` is unset, so the
    common case needs no extra configuration. The override exists because
    embeddings and completions are not guaranteed to be available from the same
    provider — see docs/DECISIONS.md.
    """

    return get_client(settings.EMBEDDING_PROVIDER or settings.LLM_PROVIDER)


def reset_llm_client() -> None:
    """Discard every cached client. Intended for tests that swap providers."""

    _clients.clear()
