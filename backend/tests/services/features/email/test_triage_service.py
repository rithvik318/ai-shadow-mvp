"""Triage, follow-ups, and the promise that no message is ever invented."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import AnalysisValidationError, EmailValidationError
from app.models.email import EmailCategory, EmailPriority
from app.models.user import User
from app.services.email.provider.base import AttachmentRef, EmailAddress, EmailMessage
from app.services.features.digital_twin import profile_service
from app.services.features.email import triage_service
from app.services.features.email.triage_service import (
    ThreadSummary,
    TriageVerdict,
    _coerce_datetime,
)
from tests.support.email import message

URGENT = TriageVerdict(
    category=EmailCategory.URGENT,
    priority=EmailPriority.HIGH,
    summary="The client needs revised numbers before Friday.",
    suggested_action="Send the revised pricing today.",
    action_items=["Send revised numbers"],
    follow_up_recommended=True,
    follow_up_reason="They are waiting on the numbers.",
    follow_up_due_at="2026-08-21T17:00:00Z",
)

FYI = TriageVerdict(
    category=EmailCategory.FYI,
    priority=EmailPriority.LOW,
    summary="A newsletter.",
    follow_up_recommended=False,
)


def _profile(db: Session, user: User) -> None:
    profile_service.upsert_profile(
        db,
        {
            "name": "Robert Keenan",
            "role": "Chief Executive",
            "organization": "SunRadia",
            "priorities": ["Close the Q3 pipeline"],
        },
        user_id=user.id,
    )


# --- classification ------------------------------------------------------


def test_a_message_is_classified_and_stored(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    assert assessment.category is EmailCategory.URGENT
    assert assessment.priority is EmailPriority.HIGH
    assert assessment.action_items == ["Send revised numbers"]
    assert assessment.provider_message_id == "m1"
    assert assessment.user_id == test_user.id


def test_the_stored_row_carries_headers_and_not_the_body(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """This is not a mailbox mirror. Enough to recognise the row, and no more —
    the body is read from the provider when it is needed."""

    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session,
        message(body="Confidential: the acquisition closes on the 30th."),
        provider="outlook",
        user_id=test_user.id,
    )

    assert assessment.subject == "Proposal follow-up"
    assert assessment.sender == "client@example.com"
    assert not hasattr(assessment, "body")
    assert "acquisition" not in (assessment.summary or "")


def test_classification_is_judged_against_this_users_twin(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """What counts as urgent follows this person's responsibilities, not a
    generic notion of importance."""

    calls = fake_analysis(URGENT)
    _profile(db_session, test_user)

    triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    prompt_name, _, variables = calls[0]

    assert prompt_name == "email_triage"
    assert "Close the Q3 pipeline" in variables["persona"]


def test_one_users_assessments_are_invisible_to_another(
    db_session: Session, test_user: User, test_user_b: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    assert triage_service.list_assessments(db_session, user_id=test_user_b.id) == []
    assert (
        triage_service.find_assessment(
            db_session, provider="outlook", message_id="m1", user_id=test_user_b.id
        )
        is None
    )


def test_reassessing_updates_the_same_row(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """Two assessments of one message would give the inbox two answers and no
    way to choose between them."""

    fake_analysis([URGENT, FYI])

    first = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )
    second = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    assert first.id == second.id
    assert second.category is EmailCategory.FYI
    assert len(triage_service.list_assessments(db_session, user_id=test_user.id)) == 1


def test_an_unpersisted_assessment_never_reaches_the_database(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """A message somebody pasted in is in no mailbox, and must not turn up in
    a follow-up list as though it were."""

    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session, message(), provider="manual", user_id=test_user.id, persist=False
    )

    assert assessment.category is EmailCategory.URGENT
    assert triage_service.list_assessments(db_session, user_id=test_user.id) == []
    assert triage_service.list_follow_ups(db_session, user_id=test_user.id) == []


def test_a_message_with_no_id_cannot_be_triaged(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    with pytest.raises(EmailValidationError):
        triage_service.assess_message(
            db_session,
            EmailMessage(message_id="", subject="Hi", body="Hello"),
            provider="outlook",
            user_id=test_user.id,
        )


def test_an_empty_message_cannot_be_triaged(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    with pytest.raises(EmailValidationError):
        triage_service.assess_message(
            db_session,
            EmailMessage(message_id="m9", subject="", body=""),
            provider="outlook",
            user_id=test_user.id,
        )


def test_an_unparsable_due_date_becomes_none_rather_than_a_guess(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """A wrong deadline is worse than no deadline — somebody plans around it."""

    fake_analysis(URGENT.model_copy(update={"follow_up_due_at": "next Friday-ish"}))

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    assert assessment.follow_up_due_at is None
    assert assessment.follow_up_recommended is True


def test_a_naive_due_date_is_read_as_utc(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(URGENT.model_copy(update={"follow_up_due_at": "2026-08-21T17:00:00"}))

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    assert assessment.follow_up_due_at == datetime(2026, 8, 21, 17, 0, tzinfo=UTC)


def test_a_single_action_item_string_is_accepted(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """Models answer with a bare string here often enough that a 502 would be
    a needless loss."""

    verdict = TriageVerdict.model_validate(
        {
            "category": "needs_reply",
            "priority": "normal",
            "summary": "They asked a question.",
            "action_items": "Answer the question",
        }
    )

    assert verdict.action_items == ["Answer the question"]


# --- rendering -----------------------------------------------------------


def test_a_message_renders_with_its_headers_and_body() -> None:
    rendered = triage_service.render_message(
        EmailMessage(
            message_id="m1",
            sender=EmailAddress(address="a@x.com", name="Ana"),
            to_recipients=[EmailAddress(address="me@sunradia.com")],
            cc_recipients=[EmailAddress(address="cc@x.com")],
            subject="Numbers",
            body="Please send them.",
            received_at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
            attachments=[
                AttachmentRef(
                    attachment_id="a1",
                    filename="scope.pdf",
                    content_type="application/pdf",
                    size_bytes=10,
                )
            ],
        )
    )

    assert "Ana <a@x.com>" in rendered
    assert "Cc: cc@x.com" in rendered
    assert "scope.pdf" in rendered
    assert "Please send them." in rendered


def test_a_very_long_body_is_truncated_rather_than_refused() -> None:
    """The opening of an email is where its purpose is. Declining to triage a
    long thread would be a worse answer than triaging its first pages."""

    rendered = triage_service.render_message(
        EmailMessage(message_id="m1", subject="Long", body="x" * 20_000)
    )

    assert len(rendered) < 20_000
    assert "x" * 100 in rendered


def test_a_thread_renders_each_message_separately() -> None:
    rendered = triage_service.render_thread(
        [
            message(message_id="m1", body="First."),
            message(message_id="m2", body="Second."),
        ]
    )

    assert "--- MESSAGE 1 ---" in rendered
    assert "--- MESSAGE 2 ---" in rendered
    assert rendered.index("First.") < rendered.index("Second.")


# --- threads -------------------------------------------------------------


def test_a_thread_is_summarised_for_this_user(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    calls = fake_analysis(
        ThreadSummary(
            summary="Pricing agreed; scope still open.",
            action_items=["Confirm the delivery date"],
        )
    )
    _profile(db_session, test_user)

    summary = triage_service.summarize_thread(
        db_session,
        [message(message_id="m1"), message(message_id="m2")],
        user_id=test_user.id,
    )

    assert summary.summary.startswith("Pricing agreed")
    assert calls[0][0] == "email_thread_summary"
    assert "Robert Keenan" in calls[0][2]["persona"]


def test_an_empty_thread_cannot_be_summarised(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(ThreadSummary(summary=""))

    with pytest.raises(EmailValidationError):
        triage_service.summarize_thread(db_session, [], user_id=test_user.id)


# --- follow-ups ----------------------------------------------------------


def test_only_recommended_follow_ups_are_listed(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis([URGENT, FYI])

    triage_service.assess_message(
        db_session, message(message_id="m1"), provider="outlook", user_id=test_user.id
    )
    triage_service.assess_message(
        db_session, message(message_id="m2"), provider="outlook", user_id=test_user.id
    )

    follow_ups = triage_service.list_follow_ups(db_session, user_id=test_user.id)

    assert [item.provider_message_id for item in follow_ups] == ["m1"]


def test_follow_ups_sort_dated_commitments_first(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """A dated commitment is the one that can be missed. An undated "should
    circle back" should not push it down the page."""

    undated = URGENT.model_copy(update={"follow_up_due_at": None})
    early = URGENT.model_copy(update={"follow_up_due_at": "2026-08-20T09:00:00Z"})
    later = URGENT.model_copy(update={"follow_up_due_at": "2026-08-25T09:00:00Z"})

    fake_analysis([undated, later, early])

    for identifier in ("m1", "m2", "m3"):
        triage_service.assess_message(
            db_session,
            message(message_id=identifier),
            provider="outlook",
            user_id=test_user.id,
        )

    follow_ups = triage_service.list_follow_ups(db_session, user_id=test_user.id)

    assert [item.provider_message_id for item in follow_ups] == ["m3", "m2", "m1"]


def test_a_handled_follow_up_leaves_the_list(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    triage_service.set_handled(
        db_session, assessment.id, handled=True, user_id=test_user.id
    )

    assert triage_service.list_follow_ups(db_session, user_id=test_user.id) == []
    assert (
        len(
            triage_service.list_follow_ups(
                db_session, include_handled=True, user_id=test_user.id
            )
        )
        == 1
    )


def test_a_follow_up_can_be_put_back(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )
    triage_service.set_handled(
        db_session, assessment.id, handled=True, user_id=test_user.id
    )
    triage_service.set_handled(
        db_session, assessment.id, handled=False, user_id=test_user.id
    )

    assert len(triage_service.list_follow_ups(db_session, user_id=test_user.id)) == 1


def test_another_users_follow_up_cannot_be_marked_handled(
    db_session: Session, test_user: User, test_user_b: User, fake_analysis
) -> None:
    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    with pytest.raises(EmailValidationError):
        triage_service.set_handled(
            db_session, assessment.id, handled=True, user_id=test_user_b.id
        )


def test_nothing_is_triaged_without_a_message(
    db_session: Session, test_user: User
) -> None:
    """The load-bearing negative: with no mailbox and no supplied message,
    there is nothing to list, because nothing is ever invented."""

    assert triage_service.list_assessments(db_session, user_id=test_user.id) == []
    assert triage_service.list_follow_ups(db_session, user_id=test_user.id) == []


# --- the real engine -----------------------------------------------------


def test_the_real_engine_validates_the_triage_reply(
    db_session: Session, test_user: User, fake_llm
) -> None:
    fake_llm(
        '{"category": "needs_reply", "priority": "normal", '
        '"summary": "They want the numbers.", "action_items": [], '
        '"follow_up_recommended": false}'
    )

    assessment = triage_service.assess_message(
        db_session, message(), provider="outlook", user_id=test_user.id
    )

    assert assessment.category is EmailCategory.NEEDS_REPLY


def test_an_invented_category_is_a_provider_error(
    db_session: Session, test_user: User, fake_llm
) -> None:
    """A category outside the five would become a label in a list nobody
    re-reads. Better to fail loudly."""

    fake_llm(
        '{"category": "extremely_urgent", "priority": "normal", '
        '"summary": "x", "action_items": []}'
    )

    with pytest.raises(AnalysisValidationError):
        triage_service.assess_message(
            db_session, message(), provider="outlook", user_id=test_user.id
        )


# --- due-date normalisation ----------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        # Naive: the model omitted an offset, and every datetime this system
        # produces is UTC, so that is what it is taken to mean.
        ("2026-08-21T17:00:00", datetime(2026, 8, 21, 17, 0, tzinfo=UTC)),
        ("2026-08-21T17:00:00Z", datetime(2026, 8, 21, 17, 0, tzinfo=UTC)),
        ("2026-08-21T17:00:00+00:00", datetime(2026, 8, 21, 17, 0, tzinfo=UTC)),
        # Offset-aware: the same instant, not the same clock reading.
        ("2026-08-21T19:00:00+02:00", datetime(2026, 8, 21, 17, 0, tzinfo=UTC)),
        ("2026-08-21T13:00:00-04:00", datetime(2026, 8, 21, 17, 0, tzinfo=UTC)),
    ],
)
def test_a_due_date_is_normalised_to_aware_utc(
    supplied: str, expected: datetime
) -> None:
    """Checked directly on the function, because the storage layer is a
    separate concern with its own tests. A naive value is labelled UTC without
    moving the clock; an offset value is converted, preserving the instant."""

    assert _coerce_datetime(supplied) == expected
    assert _coerce_datetime(supplied).tzinfo is not None


@pytest.mark.parametrize(
    "supplied", [None, "", "   ", "next Friday-ish", "soon", "2026-13-45"]
)
def test_an_unusable_due_date_is_none(supplied: str | None) -> None:
    assert _coerce_datetime(supplied) is None


# --- the transient assessment --------------------------------------------


def test_an_unpersisted_assessment_is_still_a_complete_assessment(
    db_session: Session, test_user: User, fake_analysis
) -> None:
    """It never reaches an INSERT, so the column defaults for `id` and
    `handled` never fire. Both are set explicitly — without them the response
    model rejects a result that is otherwise perfectly good."""

    fake_analysis(URGENT)

    assessment = triage_service.assess_message(
        db_session, message(), provider="manual", user_id=test_user.id, persist=False
    )

    assert assessment.id is not None
    assert assessment.handled is False
    assert assessment.assessed_at is not None
    assert assessment.user_id == test_user.id
