from types import SimpleNamespace

import pytest
from openai import OpenAIError

from app.config.settings import settings
from app.core.exceptions import EmbeddingDimensionError, EmbeddingError
from app.services.llm import client as client_module
from app.services.llm import embedding_service as embedding_module
from app.services.llm.embedding_service import embedding_service

DIMENSIONS = 4


@pytest.fixture(autouse=True)
def _small_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use narrow vectors so expectations stay readable."""

    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", DIMENSIONS)
    client_module.reset_llm_client()


def _vector(seed: float) -> list[float]:
    return [seed] * DIMENSIONS


def _install_provider(
    monkeypatch: pytest.MonkeyPatch, create
) -> list[dict[str, object]]:
    """Point the embedding service at a fake provider, recording its calls."""

    calls: list[dict[str, object]] = []

    def recording_create(**kwargs: object):
        calls.append(kwargs)
        return create(**kwargs)

    fake_client = SimpleNamespace(embeddings=SimpleNamespace(create=recording_create))
    monkeypatch.setattr(embedding_module, "get_embedding_client", lambda: fake_client)
    return calls


def _response(vectors: list[list[float]], *, shuffled: bool = False):
    items = [
        SimpleNamespace(index=index, embedding=vector)
        for index, vector in enumerate(vectors)
    ]
    if shuffled:
        items = list(reversed(items))
    return SimpleNamespace(data=items)


# --- happy path ----------------------------------------------------------


def test_embed_texts_returns_one_vector_per_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each input gets a vector back."""

    _install_provider(
        monkeypatch,
        lambda **kwargs: _response([_vector(0.1), _vector(0.2)]),
    )

    vectors = embedding_service.embed_texts(["alpha", "beta"])

    assert vectors == [_vector(0.1), _vector(0.2)]


def test_embed_texts_preserves_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vectors are ordered by the provider's index, not by arrival.

    A silent reordering would attach every vector to the wrong chunk, which no
    later stage could detect.
    """

    _install_provider(
        monkeypatch,
        lambda **kwargs: _response(
            [_vector(0.1), _vector(0.2), _vector(0.3)], shuffled=True
        ),
    )

    vectors = embedding_service.embed_texts(["a", "b", "c"])

    assert vectors == [_vector(0.1), _vector(0.2), _vector(0.3)]


def test_embed_texts_uses_the_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "some/embedding-model")
    calls = _install_provider(monkeypatch, lambda **kwargs: _response([_vector(0.1)]))

    embedding_service.embed_texts(["alpha"])

    assert calls[0]["model"] == "some/embedding-model"


def test_embed_query_returns_a_single_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_provider(monkeypatch, lambda **kwargs: _response([_vector(0.5)]))

    assert embedding_service.embed_query("a question") == _vector(0.5)


def test_embed_texts_returns_empty_for_empty_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No inputs means no provider call at all."""

    calls = _install_provider(monkeypatch, lambda **kwargs: _response([]))

    assert embedding_service.embed_texts([]) == []
    assert calls == []


# --- batching ------------------------------------------------------------


def test_embed_texts_splits_into_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inputs beyond the batch size are sent as several calls."""

    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    calls = _install_provider(
        monkeypatch,
        lambda **kwargs: _response([_vector(0.1)] * len(kwargs["input"])),
    )

    vectors = embedding_service.embed_texts(["a", "b", "c", "d", "e"])

    assert len(calls) == 3
    assert [len(call["input"]) for call in calls] == [2, 2, 1]
    assert len(vectors) == 5


def test_batched_results_are_concatenated_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batching does not disturb the overall ordering."""

    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 1)
    seeds = iter([0.1, 0.2, 0.3])
    _install_provider(monkeypatch, lambda **kwargs: _response([_vector(next(seeds))]))

    vectors = embedding_service.embed_texts(["a", "b", "c"])

    assert vectors == [_vector(0.1), _vector(0.2), _vector(0.3)]


# --- failures ------------------------------------------------------------


def test_provider_failure_raises_embedding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider exceptions surface as a domain error, not an SDK error."""

    def failing(**kwargs: object):
        raise OpenAIError("provider exploded")

    _install_provider(monkeypatch, failing)

    with pytest.raises(EmbeddingError):
        embedding_service.embed_texts(["alpha"])


def test_wrong_dimension_raises_dimension_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model whose width differs from the column is rejected before storage."""

    _install_provider(monkeypatch, lambda **kwargs: _response([[0.1, 0.2]]))

    with pytest.raises(EmbeddingDimensionError):
        embedding_service.embed_texts(["alpha"])


def test_dimension_error_names_both_widths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message has to be actionable — which model, and what it returned."""

    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "some/wrong-model")
    _install_provider(monkeypatch, lambda **kwargs: _response([[0.1, 0.2]]))

    with pytest.raises(EmbeddingDimensionError) as excinfo:
        embedding_service.embed_texts(["alpha"])

    message = str(excinfo.value)
    assert "some/wrong-model" in message
    assert "2" in message and str(DIMENSIONS) in message


def test_missing_vectors_raise_embedding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short response is an error rather than a silently truncated result."""

    _install_provider(monkeypatch, lambda **kwargs: _response([_vector(0.1)]))

    with pytest.raises(EmbeddingError):
        embedding_service.embed_texts(["alpha", "beta"])


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_is_rejected_before_calling_the_provider(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """Blank input wastes a paid call and returns a meaningless vector."""

    calls = _install_provider(monkeypatch, lambda **kwargs: _response([_vector(0.1)]))

    with pytest.raises(EmbeddingError):
        embedding_service.embed_texts([text])

    assert calls == []


# --- provider selection --------------------------------------------------


def test_embedding_provider_defaults_to_the_llm_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset EMBEDDING_PROVIDER means "same provider as completions"."""

    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", None)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    assert client_module.get_embedding_client() is client_module.get_llm_client()


def test_embedding_provider_can_differ_from_the_llm_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embeddings can fall back to OpenAI while completions stay elsewhere."""

    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")

    assert client_module.get_embedding_client() is not client_module.get_llm_client()


def test_unknown_embedding_provider_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "not-a-provider")

    with pytest.raises(ValueError):
        client_module.get_embedding_client()
