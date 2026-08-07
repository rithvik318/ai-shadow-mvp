import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmbeddingError,
    EmptyQueryError,
    LLMServiceError,
    RetrievalError,
)
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.features.chat.chat_service import (
    NO_CONTEXT_ANSWER,
    answer_question,
)
from app.services.llm.embedding_service import embedding_service

EAST = [1.0, 0.0]
NORTH_EAST = [1.0, 1.0]
NORTH = [0.0, 1.0]


def _seed(
    db: Session,
    chunks: list[tuple[str, list[float] | None]],
    *,
    filename: str = "handbook.pdf",
    status: DocumentStatus = DocumentStatus.INDEXED,
    user_id: str = "mvp-user",
    page_number: int | None = None,
    section_title: str | None = None,
) -> Document:
    """Persist a document and chunks with hand-chosen vectors."""

    document = Document(
        user_id=user_id,
        filename=filename,
        content_type="application/pdf",
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


# --- a successful answer --------------------------------------------------


def test_returns_the_models_answer(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("Holiday accrues at two days a month.")
    _seed(db_session, [("Holiday accrues monthly.", EAST)])

    result = answer_question(db_session, "How does holiday accrue?")

    assert result.answer == "Holiday accrues at two days a month."


def test_sources_mirror_the_retrieved_chunks(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """Sources come from retrieval, so they cannot be invented by the model."""

    embed_query_as(EAST)
    fake_llm("An answer.")
    document = _seed(
        db_session,
        [("Holiday accrues monthly.", EAST)],
        filename="handbook.pdf",
        page_number=12,
        section_title="Leave",
    )

    result = answer_question(db_session, "holiday?")

    assert result.retrieved_chunks == 1
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.document_id == document.id
    assert source.filename == "handbook.pdf"
    assert source.page_number == 12
    assert source.section_title == "Leave"
    assert source.similarity == pytest.approx(1.0)


def test_answer_whitespace_is_trimmed(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("\n  An answer.  \n")
    _seed(db_session, [("body", EAST)])

    assert answer_question(db_session, "q?").answer == "An answer."


def test_top_k_is_passed_through_to_retrieval(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("An answer.")
    _seed(db_session, [("a", EAST), ("b", NORTH_EAST), ("c", NORTH)])

    result = answer_question(db_session, "q?", top_k=2)

    assert result.retrieved_chunks == 2


def test_results_are_scoped_to_the_owning_user(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("An answer.")
    _seed(db_session, [("mine", EAST)], user_id="mvp-user")
    _seed(db_session, [("theirs", EAST)], user_id="someone-else")

    result = answer_question(db_session, "q?")

    assert [source.filename for source in result.sources] == ["handbook.pdf"]
    assert result.retrieved_chunks == 1


# --- what the model is actually shown -------------------------------------


def test_the_model_receives_the_registered_rag_prompt(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """The prompt comes from the registry, not from a string in this service."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed(db_session, [("body", EAST)])

    answer_question(db_session, "q?")

    messages = calls[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "only source of truth" in messages[0]["content"].lower()


def test_context_passages_are_numbered_and_attributed(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """Each passage names its document and page so the model can tell two
    similar passages apart, and qualify an answer that holds in only one."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed(
        db_session,
        [("First passage.", EAST), ("Second passage.", NORTH_EAST)],
        filename="handbook.pdf",
        page_number=3,
    )

    answer_question(db_session, "q?")

    user_message = calls[0][1]["content"]
    assert "[1] handbook.pdf, page 3" in user_message
    assert "[2] handbook.pdf, page 3" in user_message
    assert "First passage." in user_message
    assert "Second passage." in user_message


def test_a_passage_without_a_page_falls_back_to_its_heading(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """DOCX has no page numbers, so the heading is the only locator it has."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed(
        db_session,
        [("body", EAST)],
        filename="policy.docx",
        page_number=None,
        section_title="Leave",
    )
    answer_question(db_session, "q?")
    assert "[1] policy.docx, section 'Leave'" in calls[0][1]["content"]


def test_the_question_reaches_the_model(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed(db_session, [("body", EAST)])

    answer_question(db_session, "  How does holiday accrue?  ")

    assert "How does holiday accrue?" in calls[0][1]["content"]


# --- nothing to answer from ----------------------------------------------


def test_no_retrieved_chunks_answers_honestly(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """Nothing relevant is an answer, not an error."""

    embed_query_as(EAST)
    fake_llm("should not be used")
    _seed(db_session, [("unrelated", NORTH)])

    result = answer_question(db_session, "q?", top_k=5,
                             similarity_threshold=0.99)

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.sources == []
    assert result.retrieved_chunks == 0


def test_no_retrieved_chunks_does_not_call_the_model(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """Asking a model to answer with no context invites the invention the
    prompt spends five rules forbidding — and charges for it."""

    embed_query_as(EAST)
    calls = fake_llm("should not be used")
    _seed(db_session, [("unrelated", NORTH)])

    answer_question(db_session, "q?", similarity_threshold=0.99)

    assert calls == []


def test_an_empty_knowledge_base_answers_honestly(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("should not be used")

    result = answer_question(db_session, "q?")

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.retrieved_chunks == 0
    assert calls == []


@pytest.mark.parametrize(
    "status",
    [DocumentStatus.PENDING, DocumentStatus.PROCESSING, DocumentStatus.FAILED],
)
def test_documents_that_are_not_indexed_cannot_be_answered_from(
    db_session: Session, embed_query_as, fake_llm, status: DocumentStatus
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("should not be used")
    _seed(db_session, [("perfect match", EAST)], status=status)

    result = answer_question(db_session, "q?")

    assert result.answer == NO_CONTEXT_ANSWER
    assert calls == []


# --- failure paths --------------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_a_blank_question_is_rejected(
    db_session: Session, embed_query_as, fake_llm, question: str
) -> None:
    embedded = embed_query_as(EAST)
    calls = fake_llm("should not be used")
    _seed(db_session, [("body", EAST)])

    with pytest.raises(EmptyQueryError):
        answer_question(db_session, question)

    assert embedded == []
    assert calls == []


@pytest.mark.parametrize("top_k", [0, -1])
def test_an_invalid_top_k_is_rejected(
    db_session: Session, embed_query_as, fake_llm, top_k: int
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("should not be used")
    _seed(db_session, [("body", EAST)])

    with pytest.raises(RetrievalError):
        answer_question(db_session, "q?", top_k=top_k)

    assert calls == []


def test_a_retrieval_failure_propagates(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, fake_llm
) -> None:
    """A provider outage during retrieval must not become "nothing found"."""

    calls = fake_llm("should not be used")
    _seed(db_session, [("body", EAST)])

    def failing(text: str) -> list[float]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(embedding_service, "embed_query", failing)

    with pytest.raises(EmbeddingError):
        answer_question(db_session, "q?")

    assert calls == []


def test_an_llm_failure_propagates(
    db_session: Session, embed_query_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.llm.llm_service import llm_service

    embed_query_as(EAST)
    _seed(db_session, [("body", EAST)])

    def failing(messages: list) -> str:
        raise LLMServiceError("model down")

    monkeypatch.setattr(llm_service, "complete", failing)

    with pytest.raises(LLMServiceError):
        answer_question(db_session, "q?")
