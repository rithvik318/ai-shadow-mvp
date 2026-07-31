"""The same search, run against real pgvector.

The SQLite suite exercises `retrieval_service` through a registered Python
function standing in for `<=>`. That keeps the suite service-free, but a
stand-in can drift from the thing it stands in for — which is exactly how PR 1's
NULL-semantics defect survived: the test dialect quietly disagreed with
production and the suite still passed.

This module closes that gap. It runs the identical `search()` call against a
live Postgres with pgvector and asserts the same ordering and the same
similarity values, so a divergence between the shim and the real operator fails
a test instead of reaching users.

Skipped when no database is reachable, so `pytest` stays green with nothing
running. To include it:

    docker compose up -d
    cd backend && alembic upgrade head
    pytest -m postgres

Everything is created in a throwaway schema inside a transaction that is always
rolled back — Postgres DDL is transactional, so the run leaves no trace in the
development database.
"""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.base import Base
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.retrieval.retrieval_service import search
from app.services.llm.embedding_service import embedding_service

pytestmark = pytest.mark.postgres

EAST = [1.0, 0.0]
NORTH_EAST = [1.0, 1.0]
NORTH = [0.0, 1.0]


def _pad(vector: list[float]) -> list[float]:
    """Widen a hand-written vector to the column's width.

    Zero padding leaves cosine similarity unchanged, so the geometry stays
    readable while satisfying `vector(1536)`.
    """

    return vector + [0.0] * (settings.EMBEDDING_DIMENSIONS - len(vector))


@pytest.fixture
def pg_session() -> Iterator[Session]:
    """A session against a temporary schema in the real database."""

    engine = create_engine(settings.DATABASE_URL)

    try:
        connection = engine.connect()
    except SQLAlchemyError as exc:
        engine.dispose()
        pytest.skip(f"no Postgres reachable at DATABASE_URL: {exc.__class__.__name__}")

    # `begin()` comes before the first `execute()`, and that order is the whole
    # point: SQLAlchemy 2.0 autobegins a transaction on a connection's first
    # use, so probing for the extension first would leave `begin()` raising
    # "this connection has already initialized a Transaction object".
    transaction = connection.begin()

    try:
        has_pgvector = connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).first()

        if has_pgvector is None:
            pytest.skip("the 'vector' extension is not installed in this database")

        schema = f"retrieval_test_{uuid.uuid4().hex[:8]}"
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}", public'))
        Base.metadata.create_all(bind=connection)

        # The session joins the transaction above via a SAVEPOINT rather than
        # owning it, so a `commit()` inside a test releases the savepoint and
        # the outer rollback below still discards everything.
        session = Session(bind=connection, join_transaction_mode="create_savepoint")

        try:
            yield session
        finally:
            session.close()
    finally:
        # Runs on the skip path too, so a database without pgvector is left
        # exactly as it was found.
        transaction.rollback()
        connection.close()
        engine.dispose()


def _seed(session: Session, chunks: list[tuple[str, list[float]]]) -> None:
    document = Document(
        user_id="mvp-user",
        filename="atlas.pdf",
        content_type="application/pdf",
        file_size_bytes=64,
        status=DocumentStatus.INDEXED,
        chunk_count=len(chunks),
    )
    session.add(document)
    session.flush()

    session.add_all(
        DocumentChunk(
            document_id=document.id,
            user_id="mvp-user",
            chunk_index=index,
            content=content,
            char_count=len(content),
            page_number=index + 1,
            embedding=_pad(vector),
        )
        for index, (content, vector) in enumerate(chunks)
    )
    session.flush()


def test_pgvector_orders_results_the_same_way_the_sqlite_shim_does(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering asserted throughout the SQLite suite holds against `<=>`."""

    monkeypatch.setattr(embedding_service, "embed_query", lambda text: _pad(EAST))
    _seed(
        pg_session,
        [("north", NORTH), ("east", EAST), ("north east", NORTH_EAST)],
    )

    results = search(pg_session, "which way is east?")

    assert [result.content for result in results] == ["east", "north east", "north"]


def test_pgvector_reports_the_same_similarity_values(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not just the same order — the same numbers, to within float error."""

    monkeypatch.setattr(embedding_service, "embed_query", lambda text: _pad(EAST))
    _seed(pg_session, [("east", EAST), ("north east", NORTH_EAST), ("north", NORTH)])

    similarities = [result.similarity for result in search(pg_session, "east")]

    assert similarities == [
        pytest.approx(1.0, abs=1e-6),
        pytest.approx(0.7071067, abs=1e-6),
        pytest.approx(0.0, abs=1e-6),
    ]


def test_pgvector_applies_the_same_threshold(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedding_service, "embed_query", lambda text: _pad(EAST))
    _seed(pg_session, [("east", EAST), ("north east", NORTH_EAST), ("north", NORTH)])

    results = search(pg_session, "east", similarity_threshold=0.9)

    assert [result.content for result in results] == ["east"]


def test_pgvector_ignores_chunks_without_a_vector(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PR 1 defect, asserted against the dialect that actually ships."""

    monkeypatch.setattr(embedding_service, "embed_query", lambda text: _pad(EAST))
    document = Document(
        user_id="mvp-user",
        filename="atlas.pdf",
        content_type="application/pdf",
        file_size_bytes=64,
        status=DocumentStatus.INDEXED,
        chunk_count=2,
    )
    pg_session.add(document)
    pg_session.flush()
    pg_session.add_all(
        [
            DocumentChunk(
                document_id=document.id,
                user_id="mvp-user",
                chunk_index=0,
                content="embedded",
                char_count=8,
                embedding=_pad(EAST),
            ),
            DocumentChunk(
                document_id=document.id,
                user_id="mvp-user",
                chunk_index=1,
                content="not embedded",
                char_count=12,
                embedding=None,
            ),
        ]
    )
    pg_session.flush()

    results = search(pg_session, "east")

    assert [result.content for result in results] == ["embedded"]
