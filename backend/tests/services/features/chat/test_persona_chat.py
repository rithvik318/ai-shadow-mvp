"""Chat with a Digital Twin: what the model is shown, and what it must not be.

The existing chat tests assert that answers are grounded in retrieved
passages. These assert that adding a persona does not quietly become a second
kind of evidence — the profile and the memories reach the model, and neither
of them can be cited.
"""

from sqlalchemy.orm import Session

from app.models.digital_twin import MemoryType
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.features.chat.chat_service import NO_CONTEXT_ANSWER, answer_question
from app.services.features.digital_twin.memory_service import (
    create_memory,
    deactivate_memory,
)
from app.services.features.digital_twin.profile_service import upsert_profile

EAST = [1.0, 0.0]
NORTH_EAST = [1.0, 1.0]

EXECUTIVE = {
    "name": "Test Executive",
    "role": "CEO",
    "organization": "SunRadia",
    "communication_style": "Concise and executive-friendly",
    "responsibilities": ["Business development", "Strategic partnerships"],
    "expertise": ["Data modernization", "Analytics"],
    "priorities": ["Government opportunities", "Enterprise AI"],
    "decision_preferences": ["Prefer evidence-backed recommendations"],
    "current_focus": ["Freddie Mac analytics discussion"],
}


def _seed_document(
    db: Session,
    chunks: list[tuple[str, list[float]]],
    *,
    filename: str = "capabilities.pdf",
    page_number: int | None = 4,
) -> Document:
    document = Document(
        user_id="mvp-user",
        filename=filename,
        content_type="application/pdf",
        file_size_bytes=64,
        status=DocumentStatus.INDEXED,
        chunk_count=len(chunks),
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
            page_number=page_number,
            embedding=vector,
        )
        for index, (content, vector) in enumerate(chunks)
    )
    db.commit()

    return document


# --- the persona reaches the model ---------------------------------------


def test_the_profile_reaches_the_model(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "What do we do?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    assert "[DIGITAL TWIN PROFILE]" in context
    assert "Role: CEO" in context
    assert "Priorities: Government opportunities; Enterprise AI" in context


def test_active_memories_reach_the_model(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    create_memory(
        db_session,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers concise executive-facing emails.",
        importance=4,
        user_id=test_user.id,
    )
    create_memory(
        db_session,
        memory_type=MemoryType.DECISION,
        content="Prioritize government-sector opportunities.",
        importance=5,
        user_id=test_user.id,
    )
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "What should I pursue?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    assert "[DECISION] Prioritize government-sector opportunities." in context
    assert "[PREFERENCE] Prefers concise executive-facing emails." in context


def test_a_retired_memory_does_not_reach_the_model(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    """Retiring a memory has to stop it shaping answers, or it means nothing."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    retired = create_memory(
        db_session,
        memory_type=MemoryType.DECISION,
        content="Prioritize retail-sector opportunities.",
        importance=5,
        user_id=test_user.id,
    )
    deactivate_memory(db_session, retired.id, user_id=test_user.id)
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "What should I pursue?", twin_user_id=test_user.id)

    assert "retail-sector" not in calls[0][1]["content"]


# --- the persona is not evidence -----------------------------------------


def test_the_persona_is_not_numbered_as_a_source(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    """`[SOURCE n]` is the citation contract. If the profile or a memory could
    take a number, the model could cite the person as evidence for a claim
    about the documents."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    create_memory(
        db_session,
        memory_type=MemoryType.CONTEXT,
        content="Currently preparing for a Freddie Mac analytics discussion.",
        user_id=test_user.id,
    )
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "What do we do?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    # Exactly one numbered source, and it is the retrieved passage.
    assert context.count("[SOURCE ") == 1
    assert "[SOURCE 1]\nDocument: capabilities.pdf" in context
    profile_block = context[: context.index("[KNOWLEDGE SOURCES]")]
    assert "[SOURCE" not in profile_block


def test_source_numbering_is_unaffected_by_the_persona(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    """Citations have to mean the same thing with a Digital Twin as without."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    _seed_document(
        db_session, [("First passage.", EAST), ("Second passage.", NORTH_EAST)]
    )

    result = answer_question(db_session, "q?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    assert "[SOURCE 1]\nDocument: capabilities.pdf\nPage: 4" in context
    assert "[SOURCE 2]\nDocument: capabilities.pdf\nPage: 4" in context
    assert result.retrieved_chunks == 2


def test_the_persona_sits_above_the_knowledge_heading(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "q?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    assert context.index("[DIGITAL TWIN PROFILE]") < context.index(
        "[KNOWLEDGE SOURCES]"
    )
    assert context.index("[KNOWLEDGE SOURCES]") < context.index("[SOURCE 1]")


def test_the_persona_is_not_reported_as_a_source(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    """`sources` is built from retrieved chunks, and must stay that way."""

    embed_query_as(EAST)
    fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    create_memory(
        db_session,
        memory_type=MemoryType.FACT,
        content="A remembered fact.",
        user_id=test_user.id,
    )
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    result = answer_question(db_session, "q?", twin_user_id=test_user.id)

    assert len(result.sources) == 1
    assert result.sources[0].filename == "capabilities.pdf"


# --- the existing behaviour is unchanged ---------------------------------


def test_without_a_digital_twin_the_prompt_is_unchanged(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    """No profile and no memories must produce exactly the context this
    service produced before the feature existed — no stray headings."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "q?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    assert context.startswith("Context:\n[SOURCE 1]")
    assert "[DIGITAL TWIN PROFILE]" not in context
    assert "[KNOWLEDGE SOURCES]" not in context


def test_a_profile_does_not_answer_a_question_with_no_context(
    db_session: Session, test_user: User, embed_query_as, fake_llm
) -> None:
    """An empty knowledge base is still an empty knowledge base. The profile
    is not something to answer from."""

    embed_query_as(EAST)
    calls = fake_llm("should not be used")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    create_memory(
        db_session,
        memory_type=MemoryType.FACT,
        content="A remembered fact.",
        user_id=test_user.id,
    )

    result = answer_question(db_session, "q?", twin_user_id=test_user.id)

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.sources == []
    assert calls == []


def test_the_prompt_stays_within_the_context_budget(
    db_session: Session, test_user: User, embed_query_as, fake_llm, monkeypatch
) -> None:
    """The persona is taken out of the existing budget, not added to it."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "CHAT_CONTEXT_MAX_CHARS", 600)
    monkeypatch.setattr(settings_module.settings, "PERSONA_CONTEXT_MAX_CHARS", 200)
    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    _seed_document(db_session, [("A" * 300, EAST), ("B" * 300, NORTH_EAST)])

    answer_question(db_session, "q?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    # "Context:\n" and the question wrap the block; the assembled context
    # itself is what the budget governs.
    start = context.index("[DIGITAL TWIN PROFILE]")
    assembled = context[start : context.index("\n\nQuestion:")]
    assert len(assembled) <= 600 + len("[KNOWLEDGE SOURCES]\n") + 1


# --- one corpus, two people ----------------------------------------------


CRM = {
    "name": "Test CRM Manager",
    "role": "CRM Manager",
    "organization": "SunRadia",
    "communication_style": "Operational and detailed",
    "priorities": ["Pipeline management"],
    "current_focus": ["Customer follow-ups"],
}


def _seed_two_twins(db: Session, user_a: User, user_b: User) -> None:
    upsert_profile(db, dict(EXECUTIVE), user_id=user_a.id)
    create_memory(
        db,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers concise executive summaries.",
        importance=5,
        user_id=user_a.id,
    )
    upsert_profile(db, dict(CRM), user_id=user_b.id)
    create_memory(
        db,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers detailed pipeline status.",
        importance=5,
        user_id=user_b.id,
    )


def test_one_users_twin_never_reaches_anothers_prompt(
    db_session: Session, test_user: User, test_user_b: User, embed_query_as, fake_llm
) -> None:
    """The whole point of the feature. If this passes for both users, the
    boundary holds where it matters — in the text sent to the model."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed_two_twins(db_session, test_user, test_user_b)
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "How should I frame this?", twin_user_id=test_user.id)

    context = calls[0][1]["content"]
    assert "Role: CEO" in context
    assert "Prefers concise executive summaries." in context
    assert "CRM Manager" not in context
    assert "pipeline" not in context.lower()


def test_the_other_user_gets_their_own_twin(
    db_session: Session, test_user: User, test_user_b: User, embed_query_as, fake_llm
) -> None:
    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed_two_twins(db_session, test_user, test_user_b)
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "How should I frame this?", twin_user_id=test_user_b.id)

    context = calls[0][1]["content"]
    assert "Role: CRM Manager" in context
    assert "Prefers detailed pipeline status." in context
    assert "Role: CEO" not in context
    assert "executive summaries" not in context


def test_the_company_knowledge_is_the_same_for_both(
    db_session: Session, test_user: User, test_user_b: User, embed_query_as, fake_llm
) -> None:
    """Shared knowledge, isolated persona: the same question must retrieve the
    same documents and cite them identically, whoever is asking."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed_two_twins(db_session, test_user, test_user_b)
    _seed_document(
        db_session, [("First passage.", EAST), ("Second passage.", NORTH_EAST)]
    )

    first = answer_question(db_session, "q?", twin_user_id=test_user.id)
    second = answer_question(db_session, "q?", twin_user_id=test_user_b.id)

    assert first.retrieved_chunks == second.retrieved_chunks == 2
    assert [source.chunk_id for source in first.sources] == [
        source.chunk_id for source in second.sources
    ]

    for context in (calls[0][1]["content"], calls[1][1]["content"]):
        knowledge = context[context.index("[KNOWLEDGE SOURCES]") :]
        assert "[SOURCE 1]\nDocument: capabilities.pdf" in knowledge
        assert "[SOURCE 2]\nDocument: capabilities.pdf" in knowledge

    # Same sources, different persona — which is the whole architecture in one
    # assertion.
    assert calls[0][1]["content"] != calls[1][1]["content"]


def test_a_user_with_no_twin_gets_no_persona_not_someone_elses(
    db_session: Session, test_user: User, test_user_b: User, embed_query_as, fake_llm
) -> None:
    """The dangerous failure is not "no profile" — it is "somebody's profile"."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    upsert_profile(db_session, dict(EXECUTIVE), user_id=test_user.id)
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    answer_question(db_session, "q?", twin_user_id=test_user_b.id)

    context = calls[0][1]["content"]
    assert "[DIGITAL TWIN PROFILE]" not in context
    assert "Test Executive" not in context


def test_no_twin_at_all_still_answers(
    db_session: Session, embed_query_as, fake_llm
) -> None:
    """`twin_user_id=None` is the pre-Digital-Twin path, and it still works —
    which is why every RAG test that predates this feature is untouched."""

    embed_query_as(EAST)
    calls = fake_llm("An answer.")
    _seed_document(db_session, [("Sun Radia delivers MDM.", EAST)])

    result = answer_question(db_session, "q?")

    assert result.retrieved_chunks == 1
    assert "[DIGITAL TWIN PROFILE]" not in calls[0][1]["content"]
