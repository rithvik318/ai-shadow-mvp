import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import EmbeddingError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.documents.indexing_service import (
    backfill_missing_embeddings,
    count_unembedded_chunks,
    embed_document_chunks,
)
from app.services.llm.embedding_service import embedding_service
from tests.support.embeddings import deterministic_vector


def _document_with_chunks(
    db: Session, *, contents: list[str], embedded: bool = False
) -> Document:
    """Persist a document and its chunks directly, bypassing ingestion."""

    document = Document(
        user_id="mvp-user",
        filename="seeded.txt",
        content_type="text/plain",
        file_size_bytes=sum(len(c) for c in contents),
        status=DocumentStatus.INDEXED,
        chunk_count=len(contents),
    )
    db.add(document)
    db.flush()

    db.add_all(
        DocumentChunk(
            document_id=document.id,
            user_id="mvp-user",
            chunk_index=index,
            content=content,
            char_count=len(content),
            embedding=deterministic_vector(content) if embedded else None,
        )
        for index, content in enumerate(contents)
    )
    db.commit()

    return document


def _chunks_of(db: Session, document: Document) -> list[DocumentChunk]:
    return list(
        db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
        .scalars()
        .all()
    )


# --- storage semantics ---------------------------------------------------


def test_a_chunk_without_a_vector_is_stored_as_sql_null(
    db_session: Session,
) -> None:
    """ "No vector yet" must mean the same thing here as it does in production.

    Every query in this module asks `embedding IS NULL` to find work. SQLAlchemy's
    JSON type defaults to persisting None as the JSON encoding of `null`, which
    is not SQL NULL — under that default this count is 0 on SQLite while the
    identical query against pgvector returns 1, and the entire embedding
    pipeline silently does nothing in tests while appearing to succeed.
    """

    _document_with_chunks(db_session, contents=["alpha"])

    unembedded = db_session.execute(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.embedding.is_(None))
    ).scalar_one()

    assert unembedded == 1


# --- embed_document_chunks ----------------------------------------------


def test_embed_document_chunks_populates_every_chunk(db_session: Session) -> None:
    """Chunks that arrive without a vector come out with one."""

    document = _document_with_chunks(db_session, contents=["alpha", "beta"])

    embedded = embed_document_chunks(db_session, document.id)
    db_session.commit()

    assert embedded == 2
    assert all(
        chunk.embedding is not None for chunk in _chunks_of(db_session, document)
    )


def test_embedding_matches_the_chunk_it_is_stored_against(
    db_session: Session,
) -> None:
    """Vector n belongs to chunk n — the ordering has to survive storage."""

    document = _document_with_chunks(db_session, contents=["alpha", "beta", "gamma"])

    embed_document_chunks(db_session, document.id)
    db_session.commit()

    for chunk in _chunks_of(db_session, document):
        assert chunk.embedding == deterministic_vector(chunk.content)


def test_embed_document_chunks_is_idempotent(
    db_session: Session, fake_embeddings: list[list[str]]
) -> None:
    """A second run embeds nothing and makes no provider call."""

    document = _document_with_chunks(db_session, contents=["alpha", "beta"])
    embed_document_chunks(db_session, document.id)
    db_session.commit()
    fake_embeddings.clear()

    assert embed_document_chunks(db_session, document.id) == 0
    assert fake_embeddings == []


def test_only_unembedded_chunks_are_sent_to_the_provider(
    db_session: Session, fake_embeddings: list[list[str]]
) -> None:
    """Resuming a partial document does not re-pay for finished chunks."""

    document = _document_with_chunks(db_session, contents=["alpha", "beta"])
    chunks = _chunks_of(db_session, document)
    chunks[0].embedding = deterministic_vector(chunks[0].content)
    db_session.commit()
    fake_embeddings.clear()

    embed_document_chunks(db_session, document.id)

    assert fake_embeddings == [["beta"]]


def test_embedding_is_scoped_to_the_owning_user(db_session: Session) -> None:
    """Another user's document id embeds nothing."""

    document = _document_with_chunks(db_session, contents=["alpha"])

    assert embed_document_chunks(db_session, document.id, user_id="someone-else") == 0


def test_unknown_document_embeds_nothing(db_session: Session) -> None:
    import uuid

    assert embed_document_chunks(db_session, uuid.uuid4()) == 0


def test_provider_failure_propagates(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Embedding errors are not swallowed into a silent no-op."""

    document = _document_with_chunks(db_session, contents=["alpha"])

    def failing(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_texts", failing)

    with pytest.raises(EmbeddingError):
        embed_document_chunks(db_session, document.id)


# --- count_unembedded_chunks --------------------------------------------


def test_count_unembedded_chunks_counts_only_missing_vectors(
    db_session: Session,
) -> None:
    _document_with_chunks(db_session, contents=["a", "b"], embedded=True)
    _document_with_chunks(db_session, contents=["c", "d", "e"])

    assert count_unembedded_chunks(db_session) == 3


def test_count_is_zero_when_everything_is_embedded(db_session: Session) -> None:
    _document_with_chunks(db_session, contents=["a"], embedded=True)

    assert count_unembedded_chunks(db_session) == 0


# --- backfill_missing_embeddings ----------------------------------------


def test_backfill_embeds_documents_that_have_no_vectors(
    db_session: Session,
) -> None:
    """The case this exists for: documents ingested before embeddings existed."""

    _document_with_chunks(db_session, contents=["alpha", "beta"])
    _document_with_chunks(db_session, contents=["gamma"])

    result = backfill_missing_embeddings(db_session)

    assert result.documents_processed == 2
    assert result.chunks_embedded == 3
    assert count_unembedded_chunks(db_session) == 0


def test_backfill_skips_already_embedded_documents(
    db_session: Session, fake_embeddings: list[list[str]]
) -> None:
    _document_with_chunks(db_session, contents=["a", "b"], embedded=True)
    fake_embeddings.clear()

    result = backfill_missing_embeddings(db_session)

    assert result.documents_processed == 0
    assert fake_embeddings == []


def test_backfill_marks_a_completed_document_indexed(db_session: Session) -> None:
    """A document that failed at upload becomes retrievable once embedded."""

    document = _document_with_chunks(db_session, contents=["alpha"])
    document.status = DocumentStatus.FAILED
    document.error_message = "The embedding provider is unavailable."
    db_session.commit()

    backfill_missing_embeddings(db_session)
    db_session.refresh(document)

    assert document.status == DocumentStatus.INDEXED
    assert document.error_message is None


def test_backfill_honours_the_document_limit(db_session: Session) -> None:
    for _ in range(3):
        _document_with_chunks(db_session, contents=["alpha"])

    result = backfill_missing_embeddings(db_session, limit=2)

    assert result.documents_processed == 2
    assert count_unembedded_chunks(db_session) == 1


def test_backfill_keeps_completed_documents_when_one_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One provider failure must not discard the documents already done."""

    _document_with_chunks(db_session, contents=["alpha"])
    _document_with_chunks(db_session, contents=["beta"])

    calls = {"count": 0}
    original = embedding_service.embed_texts

    def failing_on_second(texts: list[str]) -> list[list[float]]:
        calls["count"] += 1
        if calls["count"] == 2:
            raise EmbeddingError("provider down")
        return original(texts)

    monkeypatch.setattr(embedding_service, "embed_texts", failing_on_second)

    result = backfill_missing_embeddings(db_session)

    assert result.documents_processed == 1
    assert result.documents_failed == 1
    assert count_unembedded_chunks(db_session) == 1
