"""A follow-up becomes one task, and stays one task.

Triage runs whenever somebody opens their inbox. The single failure that would
make this feature unusable is a task list that grows by one row per refresh, so
most of these tests are about what repeated runs must *not* do.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.email import EmailAssessment, EmailCategory, EmailPriority
from app.models.user import User
from app.services.features.tasks import email_task_service, task_service
from app.services.features.tasks.urgency import TaskPriority, TaskStatus


def _assessment(
    db: Session,
    user: User,
    *,
    message_id: str = "AAA",
    follow_up: bool = True,
    priority: EmailPriority = EmailPriority.HIGH,
    subject: str | None = "Transformer quote",
    due_at: datetime | None = None,
) -> EmailAssessment:
    assessment = EmailAssessment(
        user_id=user.id,
        provider="outlook",
        provider_message_id=message_id,
        provider_thread_id="thread-1",
        subject=subject,
        sender_name="Robert Keenan",
        sender_address="Robert.Keenan@sunradia.com",
        category=EmailCategory.NEEDS_REPLY,
        priority=priority,
        summary="Wants the quote back",
        follow_up_recommended=follow_up,
        follow_up_reason="No reply after a week",
        follow_up_due_at=due_at,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    return assessment


def test_a_recommended_follow_up_becomes_a_task(
    db_session: Session, test_user: User
) -> None:
    assessment = _assessment(db_session, test_user)

    task, created = email_task_service.sync_follow_up_task(
        db_session, assessment, user_id=test_user.id
    )

    assert created is True
    assert task is not None
    assert task.title == "Follow up: Transformer quote"
    assert task.source == "email_follow_up"
    assert task.priority is TaskPriority.HIGH


def test_the_contact_is_an_address_not_a_display_string(
    db_session: Session, test_user: User
) -> None:
    """The Robert Keenan case, carried through into the task."""

    assessment = _assessment(db_session, test_user)
    task, _ = email_task_service.sync_follow_up_task(
        db_session, assessment, user_id=test_user.id
    )

    assert task.contact_address == "Robert.Keenan@sunradia.com"
    assert task.contact_name == "Robert Keenan"
    # The joined form exists for display and is never the address.
    assert task.contact_display == "Robert Keenan <Robert.Keenan@sunradia.com>"


def test_running_triage_again_does_not_add_a_second_task(
    db_session: Session, test_user: User
) -> None:
    """The failure that would make an inbox refresh destroy the task list."""

    assessment = _assessment(db_session, test_user)

    for _ in range(5):
        email_task_service.sync_follow_up_task(
            db_session, assessment, user_id=test_user.id
        )

    assert len(task_service.list_tasks(db_session, user_id=test_user.id)) == 1


def test_a_replaced_assessment_row_still_maps_to_the_same_task(
    db_session: Session, test_user: User
) -> None:
    """The source key is the provider's message id, not the assessment's id.

    Re-triaging can replace the assessment row; the task must not become a
    second one when it does.
    """

    first = _assessment(db_session, test_user, message_id="BBB")
    task_one, _ = email_task_service.sync_follow_up_task(
        db_session, first, user_id=test_user.id
    )

    db_session.delete(first)
    db_session.commit()

    second = _assessment(db_session, test_user, message_id="BBB")
    task_two, created = email_task_service.sync_follow_up_task(
        db_session, second, user_id=test_user.id
    )

    assert created is False
    assert task_one.id == task_two.id


def test_no_follow_up_recommended_creates_nothing(
    db_session: Session, test_user: User
) -> None:
    assessment = _assessment(db_session, test_user, follow_up=False)

    task, created = email_task_service.sync_follow_up_task(
        db_session, assessment, user_id=test_user.id
    )

    assert task is None
    assert created is False
    assert task_service.list_tasks(db_session, user_id=test_user.id) == []


def test_a_withdrawn_recommendation_does_not_complete_the_task(
    db_session: Session, test_user: User
) -> None:
    """A judgement that changed its mind is not evidence the work was done."""

    assessment = _assessment(db_session, test_user, message_id="CCC")
    task, _ = email_task_service.sync_follow_up_task(
        db_session, assessment, user_id=test_user.id
    )

    assessment.follow_up_recommended = False
    db_session.commit()

    email_task_service.sync_follow_up_task(db_session, assessment, user_id=test_user.id)

    still_there = task_service.get_task(db_session, task.id, user_id=test_user.id)

    assert still_there.status is TaskStatus.TODO


def test_a_completed_follow_up_is_not_reopened_by_another_run(
    db_session: Session, test_user: User
) -> None:
    assessment = _assessment(db_session, test_user, message_id="DDD")
    task, _ = email_task_service.sync_follow_up_task(
        db_session, assessment, user_id=test_user.id
    )
    task_service.complete_task(db_session, task.id, user_id=test_user.id)

    email_task_service.sync_follow_up_task(db_session, assessment, user_id=test_user.id)

    assert (
        task_service.get_task(db_session, task.id, user_id=test_user.id).status
        is TaskStatus.COMPLETED
    )


def test_a_due_date_is_carried_across_but_never_invented(
    db_session: Session, test_user: User
) -> None:
    stated = datetime(2026, 9, 15, 17, 0, tzinfo=UTC)

    with_date = _assessment(db_session, test_user, message_id="EEE", due_at=stated)
    without = _assessment(db_session, test_user, message_id="FFF", due_at=None)

    dated, _ = email_task_service.sync_follow_up_task(
        db_session, with_date, user_id=test_user.id
    )
    undated, _ = email_task_service.sync_follow_up_task(
        db_session, without, user_id=test_user.id
    )

    assert dated.due_at.astimezone(UTC) == stated
    # No deadline stated, so none stored. An invented one would drive an amber
    # badge and an escalation off a date nobody ever gave.
    assert undated.due_at is None


def test_a_subjectless_message_falls_back_to_the_sender(
    db_session: Session, test_user: User
) -> None:
    assessment = _assessment(db_session, test_user, message_id="GGG", subject=None)

    task, _ = email_task_service.sync_follow_up_task(
        db_session, assessment, user_id=test_user.id
    )

    assert task.title == "Follow up with Robert Keenan"


def test_two_users_each_get_their_own_task_from_one_message(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    for user in (test_user, test_user_b):
        assessment = _assessment(db_session, user, message_id="HHH")
        email_task_service.sync_follow_up_task(db_session, assessment, user_id=user.id)

    assert len(task_service.list_tasks(db_session, user_id=test_user.id)) == 1
    assert len(task_service.list_tasks(db_session, user_id=test_user_b.id)) == 1
