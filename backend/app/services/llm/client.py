from openai import OpenAI

from app.config.settings import settings

# Cached per provider name rather than as a single client, because completions
# and embeddings may deliberately point at different providers — see
# `get_embedding_client()`.
_clients: dict[str, OpenAI] = {}

_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
}

# Which setting carries each provider's credential. Here rather than at a call
# site because this module is the only one allowed to know a provider's name —
# a caller asking "is a model available?" must not have to know that the answer
# depends on OPENROUTER_API_KEY today and something else tomorrow.
_API_KEY_SETTINGS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _build_client(provider: str) -> OpenAI:
    # Timeout and retries are set on every client rather than left to the SDK.
    # Its defaults — a 600-second read timeout and two automatic retries — are
    # built for a batch job, and a report that makes a dozen calls in sequence
    # inherits them as a multiplier a person waits through.
    bounds = {
        "timeout": settings.LLM_TIMEOUT_SECONDS,
        "max_retries": settings.LLM_MAX_RETRIES,
    }

    if provider == "openai":
        return OpenAI(api_key=settings.OPENAI_API_KEY, **bounds)

    if provider == "openrouter":
        return OpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=_BASE_URLS["openrouter"],
            **bounds,
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


def structured_output_options() -> dict:
    """Provider-specific options that go with a `response_format` request.

    OpenRouter routes a model to any of several providers, and not all of them
    honour `response_format`. `require_parameters` restricts routing to the
    ones that do, rather than letting the schema be silently ignored.
    """

    if settings.LLM_PROVIDER.lower() == "openrouter":
        return {"extra_body": {"provider": {"require_parameters": True}}}

    return {}


def llm_configured() -> bool:
    """Whether chat completions can actually be attempted.

    A caller deciding between a narrative and a deterministic report needs this
    and nothing else. It answers from the provider that `LLM_PROVIDER` selects,
    so a deployment configured for OpenRouter is not reported as ready because
    an unrelated OpenAI key happens to be present.
    """

    field = _API_KEY_SETTINGS.get(settings.LLM_PROVIDER.lower())

    return bool(field and getattr(settings, field, None))


def reset_llm_client() -> None:
    """Discard every cached client. Intended for tests that swap providers."""

    _clients.clear()
