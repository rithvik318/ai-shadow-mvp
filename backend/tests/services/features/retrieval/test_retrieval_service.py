import uuid

import pytest
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import EmbeddingError, EmptyQueryError, RetrievalError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.retrieval.retrieval_service import search
from app.services.llm.embedding_service import embedding_service

# Two-dimensional vectors, so every expected ordering can be read off by eye.
# Against the query EAST: cosine similarity 1.0, ~0.707, 0.0 and -1.0.
EAST = [1.0, 0.0]
NORTH_EAST = [1.0, 1.0]
NORTH = [0.0, 1.0]
WEST = [-1.0, 0.0]


def _seed(
    db: Session,
    chunks: list[tuple[str, list[float] | None]],
    *,
    filename: str = "seeded.txt",
    status: DocumentStatus = DocumentStatus.INDEXED,
    user_id: str = "mvp-user",
    page_number: int | None = None,
    section_title: str | None = None,
) -> Document:
    """Persist a document and chunks with hand-chosen vectors."""

    document = Document(
        user_id=user_id,
        filename=filename,
        content_type="text/plain",
        file_size_bytes=64,
        status=status,
        chunk_count=len(chunks),
    )
    db.add(document)
    db.flush()

    db.add_all(
        DocumentChunk(
            document_id=document.id,
            user_id=user_id,
            chunk_index=index,
            content=content,
            char_count=len(content),
            page_number=page_number,
            section_title=section_title,
            embedding=vector,
        )
        for index, (content, vector) in enumerate(chunks)
    )
    db.commit()

    return document


# --- successful retrieval ------------------------------------------------


def test_search_returns_the_closest_chunk_first(
    db_session: Session, embed_query_as
) -> None:
    """Results are ordered by similarity, nearest first."""

    embed_query_as(EAST)
    _seed(
        db_session,
        [("north", NORTH), ("east", EAST), ("north east", NORTH_EAST)],
    )

    results = search(db_session, "which way is east?")

    assert [result.content for result in results] == ["east", "north east", "north"]


def test_search_reports_similarity_not_distance(
    db_session: Session, embed_query_as
) -> None:
    """Callers get similarity in [-1, 1], not pgvector's distance."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("north east", NORTH_EAST), ("north", NORTH)])

    results = search(db_session, "east")

    assert [round(result.similarity, 4) for result in results] == [
        1.0,
        pytest.approx(0.7071, abs=1e-4),
        0.0,
    ]


def test_results_carry_the_provenance_needed_to_cite_them(
    db_session: Session, embed_query_as
) -> None:
    """A result names its document, page and heading — the whole point of
    keeping provenance on the chunk during ingestion."""

    embed_query_as(EAST)
    document = _seed(
        db_session,
        [("east", EAST)],
        filename="atlas.pdf",
        page_number=7,
        section_title="Compass Points",
    )

    result = search(db_session, "east")[0]

    assert result.document_id == document.id
    assert result.filename == "atlas.pdf"
    assert result.page_number == 7
    assert result.section_title == "Compass Points"
    assert result.chunk_index == 0
    assert isinstance(result.chunk_id, uuid.UUID)


def test_the_query_text_is_what_gets_embedded(
    db_session: Session, embed_query_as
) -> None:
    embedded = embed_query_as(EAST)
    _seed(db_session, [("east", EAST)])

    search(db_session, "  which way is east?  ")

    assert embedded == ["which way is east?"]


# --- top-k ---------------------------------------------------------------


def test_top_k_limits_the_number_of_results(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("north east", NORTH_EAST), ("north", NORTH)])

    results = search(db_session, "east", top_k=2)

    assert [result.content for result in results] == ["east", "north east"]


def test_top_k_defaults_to_configuration(
    db_session: Session, embed_query_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_TOP_K", 1)
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("north east", NORTH_EAST)])

    assert len(search(db_session, "east")) == 1


def test_asking_for_more_than_exists_returns_what_exists(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST)])

    assert len(search(db_session, "east", top_k=50)) == 1


@pytest.mark.parametrize("top_k", [0, -1])
def test_invalid_top_k_is_rejected(
    db_session: Session, embed_query_as, top_k: int
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST)])

    with pytest.raises(RetrievalError):
        search(db_session, "east", top_k=top_k)


# --- threshold -----------------------------------------------------------


def test_threshold_excludes_chunks_below_the_floor(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("north east", NORTH_EAST), ("north", NORTH)])

    results = search(db_session, "east", similarity_threshold=0.9)

    assert [result.content for result in results] == ["east"]


def test_default_threshold_excludes_opposing_chunks(
    db_session: Session, embed_query_as
) -> None:
    """The 0.0 default means "orthogonal or better", so a chunk pointing the
    opposite way is never offered as a source."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("west", WEST)])

    assert [result.content for result in search(db_session, "east")] == ["east"]


def test_threshold_can_be_lowered_to_include_everything(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("west", WEST)])

    results = search(db_session, "east", similarity_threshold=-1.0)

    assert [result.content for result in results] == ["east", "west"]


def test_no_matching_documents_returns_an_empty_list(
    db_session: Session, embed_query_as
) -> None:
    """A corpus with nothing similar enough is not an error."""

    embed_query_as(EAST)
    _seed(db_session, [("north", NORTH)])

    assert search(db_session, "east", similarity_threshold=0.9) == []


@pytest.mark.parametrize("threshold", [1.5, -1.5, 2.0])
def test_invalid_threshold_is_rejected(
    db_session: Session, embed_query_as, threshold: float
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST)])

    with pytest.raises(RetrievalError):
        search(db_session, "east", similarity_threshold=threshold)


# --- empty and degenerate corpora ----------------------------------------


def test_empty_database_returns_an_empty_list(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)

    assert search(db_session, "east") == []


def test_empty_database_does_not_call_the_provider(
    db_session: Session, embed_query_as
) -> None:
    """A fresh install should not pay for — or fail on — an embedding call
    when there is provably nothing to search."""

    embedded = embed_query_as(EAST)

    search(db_session, "east")

    assert embedded == []


def test_chunks_without_a_vector_are_ignored(
    db_session: Session, embed_query_as
) -> None:
    """Un-embedded chunks are invisible to search rather than ranked last."""

    embed_query_as(EAST)
    _seed(db_session, [("embedded", EAST), ("not embedded", None)])

    assert [result.content for result in search(db_session, "east")] == ["embedded"]


def test_a_vector_of_the_wrong_width_is_ignored(
    db_session: Session, embed_query_as
) -> None:
    """Postgres refuses these at the column; SQLite cannot, so the query has
    to exclude them rather than crash or sort them arbitrarily."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("malformed", [1.0, 0.0, 0.0])])

    assert [result.content for result in search(db_session, "east")] == ["east"]


def test_a_zero_vector_is_ignored(db_session: Session, embed_query_as) -> None:
    """A zero vector has no direction, so its similarity is undefined."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("zero", [0.0, 0.0])])

    assert [result.content for result in search(db_session, "east")] == ["east"]


def test_a_corpus_of_only_malformed_vectors_returns_empty(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("zero", [0.0, 0.0])])

    assert search(db_session, "east") == []


# --- eligibility ---------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [DocumentStatus.PENDING, DocumentStatus.PROCESSING, DocumentStatus.FAILED],
)
def test_only_indexed_documents_are_searched(
    db_session: Session, embed_query_as, status: DocumentStatus
) -> None:
    """A document mid-ingestion, or one whose embedding failed, would answer
    from a partial view of itself."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST)], status=status)

    assert search(db_session, "east") == []


def test_an_unindexed_document_is_skipped_even_when_it_is_the_best_match(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("perfect but failed", EAST)], status=DocumentStatus.FAILED)
    _seed(db_session, [("worse but indexed", NORTH_EAST)])

    results = search(db_session, "east")

    assert [result.content for result in results] == ["worse but indexed"]


def test_results_are_scoped_to_the_owning_user(
    db_session: Session, embed_query_as
) -> None:
    embed_query_as(EAST)
    _seed(db_session, [("mine", EAST)], user_id="mvp-user")
    _seed(db_session, [("theirs", EAST)], user_id="someone-else")

    assert [result.content for result in search(db_session, "east")] == ["mine"]


def test_another_users_corpus_does_not_count_as_a_corpus(
    db_session: Session, embed_query_as
) -> None:
    embedded = embed_query_as(EAST)
    _seed(db_session, [("theirs", EAST)], user_id="someone-else")

    assert search(db_session, "east") == []
    assert embedded == []


# --- duplication ---------------------------------------------------------


def test_a_chunk_is_never_returned_twice(db_session: Session, embed_query_as) -> None:
    """The join to `documents` is many-to-one, so it must not fan out."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("north east", NORTH_EAST)])

    results = search(db_session, "east", top_k=10)

    assert len({result.chunk_id for result in results}) == len(results)


def test_identical_content_is_returned_once(
    db_session: Session, embed_query_as
) -> None:
    """Repeated boilerplate — headers, footers — should not consume several
    of the slots a caller asked for."""

    embed_query_as(EAST)
    _seed(
        db_session,
        [
            ("CONFIDENTIAL", EAST),
            ("CONFIDENTIAL", NORTH_EAST),
            ("actual content", NORTH),
        ],
    )

    results = search(db_session, "east", top_k=2)

    assert [result.content for result in results] == [
        "CONFIDENTIAL",
        "actual content",
    ]


# --- failure paths -------------------------------------------------------


def test_provider_failure_propagates(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider outage must not be reported as "nothing found"."""

    _seed(db_session, [("east", EAST)])

    def failing(text: str) -> list[float]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_query", failing)

    with pytest.raises(EmbeddingError):
        search(db_session, "east")


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_a_blank_query_is_rejected_without_calling_the_provider(
    db_session: Session, embed_query_as, query: str
) -> None:
    embedded = embed_query_as(EAST)
    _seed(db_session, [("east", EAST)])

    with pytest.raises(EmptyQueryError):
        search(db_session, query)

    assert embedded == []


# --- disabling the threshold ---------------------------------------------


def test_threshold_can_be_disabled_for_a_single_call(
    db_session: Session, embed_query_as
) -> None:
    """`None` means "no floor", distinct from "not supplied"."""

    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("west", WEST)])

    results = search(db_session, "east", similarity_threshold=None)

    assert [result.content for result in results] == ["east", "west"]


def test_threshold_can_be_disabled_by_configuration(
    db_session: Session, embed_query_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty RETRIEVAL_SIMILARITY_THRESHOLD turns filtering off globally."""

    monkeypatch.setattr(settings, "RETRIEVAL_SIMILARITY_THRESHOLD", None)
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("west", WEST)])

    assert [result.content for result in search(db_session, "east")] == [
        "east",
        "west",
    ]


def test_an_explicit_threshold_overrides_a_disabled_default(
    db_session: Session, embed_query_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_SIMILARITY_THRESHOLD", None)
    embed_query_as(EAST)
    _seed(db_session, [("east", EAST), ("west", WEST)])

    results = search(db_session, "east", similarity_threshold=0.5)

    assert [result.content for result in results] == ["east"]


def test_a_disabled_threshold_still_excludes_malformed_vectors(
    db_session: Session, embed_query_as
) -> None:
    """The NULL-distance filter is independent of the floor.

    Excluding undefined similarities used to be a side effect of the threshold
    comparison; with the floor disabled that side effect is gone, so the filter
    has to stand on its own.
    """

    embed_query_as(EAST)
    _seed(
        db_session,
        [("east", EAST), ("zero", [0.0, 0.0]), ("wrong width", [1.0, 0.0, 0.0])],
    )

    results = search(db_session, "east", similarity_threshold=None)

    assert [result.content for result in results] == ["east"]


# --- deduplication must not cost the caller results ----------------------


def test_deduplication_still_returns_the_requested_number_of_chunks(
    db_session: Session, embed_query_as
) -> None:
    """Duplicates are dropped, but the caller still gets `top_k` unique chunks."""

    embed_query_as(EAST)
    _seed(
        db_session,
        [
            ("BOILERPLATE", EAST),
            ("BOILERPLATE", EAST),
            ("BOILERPLATE", EAST),
            ("alpha", NORTH_EAST),
            ("beta", NORTH),
        ],
    )

    results = search(db_session, "east", top_k=3)

    assert [result.content for result in results] == ["BOILERPLATE", "alpha", "beta"]


def test_deduplication_pages_past_the_first_batch_to_fill_top_k(
    db_session: Session, embed_query_as
) -> None:
    """More duplicates than one page holds must not truncate the result.

    The first page here is entirely boilerplate, so filling `top_k` is only
    possible by fetching further down the ranking.
    """

    embed_query_as(EAST)
    _seed(
        db_session,
        [("BOILERPLATE", EAST)] * 12 + [("alpha", NORTH_EAST), ("beta", NORTH)],
    )

    results = search(db_session, "east", top_k=2)

    assert [result.content for result in results] == ["BOILERPLATE", "alpha"]


def test_a_corpus_of_only_duplicates_terminates(
    db_session: Session, embed_query_as
) -> None:
    """Exhausting the corpus ends the search rather than paging forever."""

    embed_query_as(EAST)
    _seed(db_session, [("BOILERPLATE", EAST)] * 15)

    results = search(db_session, "east", top_k=5)

    assert [result.content for result in results] == ["BOILERPLATE"]


# --- metadata contract ----------------------------------------------------


def test_every_result_carries_the_full_metadata_contract(
    db_session: Session, embed_query_as
) -> None:
    """document_id, filename, page number, chunk_id, similarity, chunk text —
    plus the heading, which citations use where a format has no pages."""

    embed_query_as(EAST)
    document = _seed(
        db_session,
        [("east", EAST)],
        filename="atlas.pdf",
        page_number=7,
        section_title="Compass Points",
    )

    result = search(db_session, "east")[0]

    assert result.document_id == document.id
    assert result.filename == "atlas.pdf"
    assert result.page_number == 7
    assert isinstance(result.chunk_id, uuid.UUID)
    assert result.similarity == pytest.approx(1.0)
    assert result.content == "east"
    assert result.chunk_index == 0
    assert result.section_title == "Compass Points"
