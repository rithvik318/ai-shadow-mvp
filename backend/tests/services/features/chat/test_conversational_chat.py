"""Greetings and questions about the twin, answered from the persona.

Regression for production: with an empty knowledge base, "Hi" and "Introduce
yourself" both came back as "I could not find anything in your documents",
because every message went through retrieval and an empty result short-circuited
before the profile was even loaded.
"""

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import LLMServiceError
from app.models.digital_twin import MemoryType
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.features.chat.chat_service import (
    NO_CONTEXT_ANSWER,
    NO_PROFILE_ANSWER,
    AnswerBasis,
    answer_question,
)
from app.services.features.digital_twin.memory_service import create_memory
from app.services.features.digital_twin.profile_service import upsert_profile
from app.services.llm.embedding_service import embedding_service
from app.services.llm.llm_service import llm_service

EAST = [1.0, 0.0]

PROFILE = {
    "name": "Azmat Shaik",
    "role": "Recruiter",
    "organization": "SunRadia",
    "communication_style": "Friendly and direct",
    "responsibilities": ["Candidate sourcing"],
    "expertise": ["Technical recruiting"],
    "priorities": ["Freddie Mac roles"],
    "decision_preferences": [],
    "current_focus": ["Java developer pipeline"],
}


@pytest.fixture
def no_embedding(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any attempt to embed: a conversational message must not search."""

    embedded: list[str] = []

    def record(text: str) -> list[float]:
        embedded.append(text)
        return EAST

    monkeypatch.setattr(embedding_service, "embed_query", record)
    return embedded


def test_a_greeting_gets_a_conversational_reply_with_an_empty_knowledge_base(
    db_session: Session, test_user: User, fake_llm, no_embedding: list[str]
) -> None:
    calls = fake_llm("Hi! I'm Azmat's Digital Twin. How can I help?")
    upsert_profile(db_session, dict(PROFILE), user_id=test_user.id)

    result = answer_question(db_session, "Hi", twin_user_id=test_user.id)

    assert result.answer == "Hi! I'm Azmat's Digital Twin. How can I help?"
    assert result.answer != NO_CONTEXT_ANSWER
    assert result.basis is AnswerBasis.CONVERSATION
    assert result.sources == []
    assert result.retrieved_chunks == 0
    assert len(calls) == 1
    assert no_embedding == []


def test_a_greeting_without_a_profile_still_gets_a_reply(
    db_session: Session, test_user: User, fake_llm, no_embedding: list[str]
) -> None:
    calls = fake_llm("Hello!")

    result = answer_question(db_session, "Hello", twin_user_id=test_user.id)

    assert result.answer == "Hello!"
    assert result.basis is AnswerBasis.CONVERSATION
    assert "(no profile or memories recorded)" in calls[0][1]["content"]


def test_introduce_yourself_is_answered_from_the_profile_and_memories(
    db_session: Session, test_user: User, fake_llm, no_embedding: list[str]
) -> None:
    calls = fake_llm("I'm Azmat Shaik, a recruiter at SunRadia.")
    upsert_profile(db_session, dict(PROFILE), user_id=test_user.id)
    create_memory(
        db_session,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers short status updates.",
        user_id=test_user.id,
    )

    result = answer_question(
        db_session, "Please introduce yourself", twin_user_id=test_user.id
    )

    assert result.answer == "I'm Azmat Shaik, a recruiter at SunRadia."
    assert result.basis is AnswerBasis.PROFILE
    assert no_embedding == []

    system, user = calls[0]
    assert system["content"].startswith("You are the Digital Twin")
    assert "Role: Recruiter" in user["content"]
    assert "Prefers short status updates." in user["content"]
    assert "Please introduce yourself" in user["content"]


def test_the_profile_is_never_presented_as_a_document_source(
    db_session: Session, test_user: User, fake_llm, no_embedding: list[str]
) -> None:
    calls = fake_llm("I recruit for SunRadia.")
    upsert_profile(db_session, dict(PROFILE), user_id=test_user.id)

    result = answer_question(
        db_session, "What is your role?", twin_user_id=test_user.id
    )

    assert result.sources == []
    assert result.retrieved_chunks == 0
    prompt = calls[0][1]["content"]
    assert "[SOURCE" not in prompt
    assert "[KNOWLEDGE SOURCES]" not in prompt


def test_a_question_about_the_twin_with_no_profile_is_not_invented(
    db_session: Session, test_user: User, fake_llm, no_embedding: list[str]
) -> None:
    calls = fake_llm("should not be used")

    result = answer_question(db_session, "Who are you?", twin_user_id=test_user.id)

    assert result.answer == NO_PROFILE_ANSWER
    assert result.basis is AnswerBasis.PROFILE
    assert calls == []


def test_only_the_selected_twins_profile_is_used(
    db_session: Session,
    test_user: User,
    test_user_b: User,
    fake_llm,
    no_embedding: list[str],
) -> None:
    calls = fake_llm("reply")
    upsert_profile(db_session, dict(PROFILE), user_id=test_user.id)
    upsert_profile(
        db_session,
        {**PROFILE, "name": "Someone Else", "role": "Accountant"},
        user_id=test_user_b.id,
    )

    answer_question(db_session, "Introduce yourself", twin_user_id=test_user_b.id)

    prompt = calls[0][1]["content"]
    assert "Role: Accountant" in prompt
    assert "Role: Recruiter" not in prompt


def test_a_document_question_with_no_documents_still_gets_the_no_match_reply(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("should not be used")
    upsert_profile(db_session, dict(PROFILE), user_id=test_user.id)

    result = answer_question(
        db_session, "What does the handbook say about leave?", twin_user_id=test_user.id
    )

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.basis is AnswerBasis.NO_MATCH
    assert calls == []


def test_a_grounded_answer_reports_the_documents_basis(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    fake_llm("Two days a month.")
    document = Document(
        user_id="mvp-user",
        filename="handbook.pdf",
        content_type="application/pdf",
        file_size_bytes=64,
        status=DocumentStatus.INDEXED,
        chunk_count=1,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentChunk(
            document_id=document.id,
            user_id="mvp-user",
            chunk_index=0,
            content="Holiday accrues monthly.",
            char_count=24,
            embedding=EAST,
        )
    )
    db_session.commit()

    result = answer_question(
        db_session, "How does holiday accrue?", twin_user_id=test_user.id
    )

    assert result.basis is AnswerBasis.DOCUMENTS
    assert result.retrieved_chunks == 1


def test_a_model_failure_on_a_greeting_propagates(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
    no_embedding: list[str],
) -> None:
    """Never disguised as a reply or as "nothing found"."""

    def failing(messages: list) -> str:
        raise LLMServiceError("model down")

    monkeypatch.setattr(llm_service, "complete", failing)

    with pytest.raises(LLMServiceError):
        answer_question(db_session, "Hi", twin_user_id=test_user.id)
