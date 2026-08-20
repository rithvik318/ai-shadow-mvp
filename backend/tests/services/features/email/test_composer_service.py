"""Generation: whose voice it writes in, and what it is allowed to claim.

Two properties carry this module, and both are asserted against the *prompt
variables* rather than against a reply the test chose:

- the persona in the prompt belongs to the user who asked, and to nobody else
- when retrieval finds nothing, the model is explicitly told so

`fake_analysis` records `(prompt_name, response_model, variables)` per call,
which is what makes those assertions possible. One test at the end deliberately
goes through the real `AnalysisEngine` with a faked completion, so the prompt
templates themselves are exercised rather than bypassed.
"""

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import AnalysisValidationError, EmailValidationError
from app.models.digital_twin import MemoryType
from app.models.user import User
from app.services.features.digital_twin import memory_service, profile_service
from app.services.features.email import composer_service
from app.services.features.email.composer_service import (
    NO_KNOWLEDGE,
    ComposedEmail,
    EmailOperation,
    GeneratedEmail,
    GeneratedSubject,
)

GENERATED = GeneratedEmail(subject="Following up", body="Hello — quick note.")


def _profile(db: Session, user: User, **overrides: object) -> None:
    values: dict = {
        "name": "Robert Keenan",
        "role": "Chief Executive",
        "organization": "SunRadia",
        "communication_style": "Direct, warm, never more than five sentences.",
        "priorities": ["Close the Q3 pipeline"],
    }
    values.update(overrides)

    profile_service.upsert_profile(db, values, user_id=user.id)


def _compose(db: Session, user: User, **overrides: object) -> ComposedEmail:
    values: dict = {
        "operation": EmailOperation.GENERATE,
        "instruction": "Write a concise follow-up after yesterday's meeting.",
        "twin_user_id": user.id,
    }
    values.update(overrides)

    return composer_service.compose(db, **values)


# --- Digital Twin personalisation ----------------------------------------


def test_generation_uses_the_current_users_profile(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)
    _profile(db_session, test_user)

    result = _compose(db_session, test_user)

    prompt_name, _, variables = calls[0]

    assert prompt_name == "email_compose"
    assert "Robert Keenan" in variables["persona"]
    assert "Chief Executive" in variables["persona"]
    assert result.persona_used is True


def test_generation_uses_the_current_users_memories(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)
    _profile(db_session, test_user)
    memory_service.create_memory(
        db_session,
        memory_type=MemoryType.PREFERENCE,
        content="Never open an email with 'I hope this finds you well'.",
        importance=5,
        user_id=test_user.id,
    )

    _compose(db_session, test_user)

    assert "I hope this finds you well" in calls[0][2]["persona"]


def test_two_users_get_different_prompts_from_one_instruction(
    db_session: Session, test_user: User, test_user_b: User, fake_analysis
) -> None:
    """The whole point of a Digital Twin. Same instruction, same shared corpus,
    different person writing."""

    calls = fake_analysis(GENERATED)
    _profile(db_session, test_user)
    _profile(
        db_session,
        test_user_b,
        name="Dana Osei",
        role="CRM Manager",
        communication_style="Detailed and structured.",
    )

    _compose(db_session, test_user)
    _compose(db_session, test_user_b)

    first, second = calls[0][2]["persona"], calls[1][2]["persona"]

    assert "Robert Keenan" in first
    assert "Robert Keenan" not in second
    assert "Dana Osei" in second


def test_one_users_memories_never_reach_anothers_draft(
    db_session: Session, test_user: User, test_user_b: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)
    _profile(db_session, test_user)
    _profile(db_session, test_user_b, name="Dana Osei", role="CRM Manager")
    memory_service.create_memory(
        db_session,
        memory_type=MemoryType.DECISION,
        content="We do not discount below 15 percent.",
        user_id=test_user.id,
    )

    _compose(db_session, test_user_b)

    assert "15 percent" not in calls[0][2]["persona"]


def test_a_user_with_no_profile_still_gets_a_draft(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """A missing profile is a normal state on a fresh install, not an error.
    The prompt says so rather than leaving the model to invent a role."""

    calls = fake_analysis(GENERATED)

    result = _compose(db_session, test_user)

    assert result.persona_used is False
    assert "No profile has been set up" in calls[0][2]["persona"]


def test_no_twin_means_no_profile_lookup_at_all(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """`twin_user_id=None` must never mean "whichever profile is lying
    around"."""

    calls = fake_analysis(GENERATED)
    _profile(db_session, test_user)

    result = _compose(db_session, test_user, twin_user_id=None)

    assert result.persona_used is False
    assert "Robert Keenan" not in calls[0][2]["persona"]


# --- knowledge base grounding --------------------------------------------


def test_knowledge_is_not_retrieved_unless_asked_for(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)

    result = _compose(db_session, test_user, use_knowledge_base=False)

    assert result.knowledge_used is False
    assert result.sources == []
    assert calls[0][2]["knowledge"] == NO_KNOWLEDGE


def test_an_empty_corpus_tells_the_model_to_claim_nothing(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """The anti-hallucination guarantee. An empty knowledge section would read
    as "nothing to say here"; this has to read as "you have been told nothing,
    so claim nothing"."""

    calls = fake_analysis(GENERATED)

    result = _compose(
        db_session,
        test_user,
        instruction="Draft an outreach email describing our AI and ML services.",
        use_knowledge_base=True,
    )

    assert result.knowledge_used is False
    assert result.sources == []
    assert (
        "Do not state any fact about the sender's company" in calls[0][2]["knowledge"]
    )


def test_retrieved_passages_reach_the_prompt_and_are_reported(
    db_session: Session, test_user: User, fake_analysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sources are built from retrieval, never from the model's output — so a
    source cannot be fabricated, exactly as in chat."""

    import uuid as uuid_module

    from app.services.features.retrieval.retrieval_service import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id=uuid_module.uuid4(),
        document_id=uuid_module.uuid4(),
        filename="capabilities.pdf",
        chunk_index=0,
        content="SunRadia delivers predictive maintenance analytics.",
        similarity=0.82,
        page_number=3,
        section_title="Analytics",
    )

    monkeypatch.setattr(
        composer_service.retrieval_service,
        "search",
        lambda *args, **kwargs: [chunk],
    )
    calls = fake_analysis(GENERATED)

    result = _compose(
        db_session,
        test_user,
        instruction="Draft a proposal follow-up mentioning our analytics work.",
        use_knowledge_base=True,
    )

    assert result.knowledge_used is True
    assert [source.filename for source in result.sources] == ["capabilities.pdf"]
    assert "predictive maintenance analytics" in calls[0][2]["knowledge"]
    assert "[SOURCE 1]" in calls[0][2]["knowledge"]


def test_a_retrieval_failure_is_not_silently_written_around(
    db_session: Session, test_user: User, fake_analysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swallowing this would produce a plausible email missing exactly the
    substance it was asked for."""

    from app.core.exceptions import EmbeddingError

    def explode(*args: object, **kwargs: object) -> None:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(composer_service.retrieval_service, "search", explode)
    fake_analysis(GENERATED)

    with pytest.raises(EmbeddingError):
        _compose(db_session, test_user, use_knowledge_base=True)


# --- operations ----------------------------------------------------------


def test_generate_requires_an_instruction(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    with pytest.raises(EmailValidationError):
        _compose(db_session, test_user, instruction="   ")


@pytest.mark.parametrize(
    "operation",
    [
        EmailOperation.REWRITE,
        EmailOperation.IMPROVE,
        EmailOperation.SHORTEN,
        EmailOperation.EXPAND,
        EmailOperation.PROFESSIONAL,
        EmailOperation.CONCISE,
    ],
)
def test_every_revision_operation_runs_the_transform_prompt(
    db_session: Session, test_user: User, fake_analysis, operation: EmailOperation
) -> None:
    calls = fake_analysis(GeneratedEmail(subject="Shorter", body="Short."))

    result = composer_service.compose(
        db_session,
        operation=operation,
        subject="Following up",
        body="A long and rather over-explained message about the proposal.",
        twin_user_id=test_user.id,
    )

    prompt_name, _, variables = calls[0]

    assert prompt_name == "email_transform"
    assert result.operation is operation
    assert variables["body"].startswith("A long and rather")
    # Each operation contributes its own sentence, so the model is not left to
    # infer "shorten" from the word alone.
    assert variables["instruction"].strip()


def test_a_revision_needs_something_to_revise(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    with pytest.raises(EmailValidationError):
        composer_service.compose(
            db_session,
            operation=EmailOperation.SHORTEN,
            body="   ",
            twin_user_id=test_user.id,
        )


def test_change_tone_requires_a_tone(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    with pytest.raises(EmailValidationError):
        composer_service.compose(
            db_session,
            operation=EmailOperation.CHANGE_TONE,
            body="Some text.",
            twin_user_id=test_user.id,
        )


def test_change_tone_puts_the_requested_tone_in_the_prompt(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)

    composer_service.compose(
        db_session,
        operation=EmailOperation.CHANGE_TONE,
        body="Some text.",
        tone="apologetic but firm",
        twin_user_id=test_user.id,
    )

    assert "apologetic but firm" in calls[0][2]["instruction"]


def test_a_caller_instruction_refines_rather_than_replaces_the_operation(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)

    composer_service.compose(
        db_session,
        operation=EmailOperation.SHORTEN,
        body="A long message about pricing and timelines.",
        instruction="drop the pricing paragraph",
        twin_user_id=test_user.id,
    )

    instruction = calls[0][2]["instruction"]

    assert "Shorten this email" in instruction
    assert "drop the pricing paragraph" in instruction


def test_a_revision_keeps_the_subject_when_the_model_returns_none(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """Blanking a subject somebody wrote, because a revision prompt omitted it,
    is a silent data loss."""

    fake_analysis(GeneratedEmail(subject="", body="Short."))

    result = composer_service.compose(
        db_session,
        operation=EmailOperation.SHORTEN,
        subject="Q3 proposal",
        body="A long message.",
        twin_user_id=test_user.id,
    )

    assert result.subject == "Q3 proposal"


def test_subject_generation_leaves_the_body_untouched(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """This operation writes a subject. Returning a regenerated body would
    quietly discard whatever the person had just edited."""

    calls = fake_analysis(GeneratedSubject(subject="Q3 proposal — next steps"))
    body = "Here is the revised scope, as discussed."

    result = composer_service.compose(
        db_session,
        operation=EmailOperation.SUBJECT,
        body=body,
        twin_user_id=test_user.id,
    )

    assert calls[0][0] == "email_subject"
    assert result.subject == "Q3 proposal — next steps"
    assert result.body == body


def test_a_reply_needs_the_message_it_replies_to(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    with pytest.raises(EmailValidationError):
        composer_service.compose(
            db_session,
            operation=EmailOperation.REPLY,
            instruction="Say yes and propose Thursday.",
            twin_user_id=test_user.id,
        )


def test_a_reply_puts_the_original_message_in_the_prompt(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)

    composer_service.compose(
        db_session,
        operation=EmailOperation.REPLY,
        instruction="Say yes and propose Thursday.",
        source_subject="Can we meet?",
        source_body="Are you free this week?",
        source_sender="client@example.com",
        twin_user_id=test_user.id,
    )

    source = calls[0][2]["source_email"]

    assert "client@example.com" in source
    assert "Are you free this week?" in source


def test_a_generation_is_never_a_reply_by_accident(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(GENERATED)

    _compose(db_session, test_user)

    assert calls[0][2]["source_email"] == "None. This is not a reply."


# --- the real engine -----------------------------------------------------


def test_the_real_engine_renders_the_prompt_and_parses_the_reply(
    db_session: Session, test_user: User, fake_llm
) -> None:
    """One test that does not stub the engine.

    Everything above asserts on prompt variables; this asserts that those
    variables actually render through the registered template, and that a
    fenced JSON reply — which models produce constantly — is parsed rather than
    returned as an email body full of backticks.
    """

    calls = fake_llm('```json\n{"subject": "Following up", "body": "Quick note."}\n```')
    _profile(db_session, test_user)

    result = _compose(db_session, test_user)

    assert result.subject == "Following up"
    assert result.body == "Quick note."

    system, user = calls[0][0]["content"], calls[0][1]["content"]

    assert "Robert Keenan" in user
    assert "Never state a fact about the sender's company" in system


def test_a_reply_that_is_not_json_is_a_provider_error(
    db_session: Session, test_user: User, fake_llm
) -> None:
    """Better a 502 than an email body containing the model's apology."""

    fake_llm("I'm sorry, I can't help with that.")

    with pytest.raises(AnalysisValidationError):
        _compose(db_session, test_user)
