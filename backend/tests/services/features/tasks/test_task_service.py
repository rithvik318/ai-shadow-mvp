"""Task CRUD, isolation between people, and the duplicate-prevention rule."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import TaskNotFoundError, TaskValidationError
from app.models.user import User
from app.services.features.tasks import task_service
from app.services.features.tasks.urgency import TaskPriority, TaskStatus


def test_a_task_round_trips(db_session: Session, test_user: User) -> None:
    created = task_service.create_task(
        db_session, user_id=test_user.id, title="Send the pro forma"
    )
    found = task_service.get_task(db_session, created.id, user_id=test_user.id)

    assert found.title == "Send the pro forma"
    assert found.status is TaskStatus.TODO
    assert found.completed_at is None


def test_an_empty_title_is_refused(db_session: Session, test_user: User) -> None:
    with pytest.raises(TaskValidationError):
        task_service.create_task(db_session, user_id=test_user.id, title="   ")


# --- isolation -----------------------------------------------------------


def test_one_users_tasks_are_invisible_to_another(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    task_service.create_task(db_session, user_id=test_user.id, title="Mine")

    assert task_service.list_tasks(db_session, user_id=test_user_b.id) == []


def test_another_users_task_is_not_found_rather_than_forbidden(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """A 403 would confirm the row exists. Not found is the honest answer."""

    mine = task_service.create_task(db_session, user_id=test_user.id, title="Mine")

    with pytest.raises(TaskNotFoundError):
        task_service.get_task(db_session, mine.id, user_id=test_user_b.id)


def test_one_user_cannot_complete_anothers_task(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    mine = task_service.create_task(db_session, user_id=test_user.id, title="Mine")

    with pytest.raises(TaskNotFoundError):
        task_service.complete_task(db_session, mine.id, user_id=test_user_b.id)

    assert (
        task_service.get_task(db_session, mine.id, user_id=test_user.id).status
        is TaskStatus.TODO
    )


def test_one_user_cannot_delete_anothers_task(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    mine = task_service.create_task(db_session, user_id=test_user.id, title="Mine")

    with pytest.raises(TaskNotFoundError):
        task_service.delete_task(db_session, mine.id, user_id=test_user_b.id)


# --- completion ----------------------------------------------------------


def test_completing_stamps_the_time(db_session: Session, test_user: User) -> None:
    task = task_service.create_task(db_session, user_id=test_user.id, title="Thing")
    completed = task_service.complete_task(db_session, task.id, user_id=test_user.id)

    assert completed.status is TaskStatus.COMPLETED
    assert completed.completed_at is not None


def test_reopening_clears_the_completion_stamp(
    db_session: Session, test_user: User
) -> None:
    """A reopened task that still claims a completion time is a lie."""

    task = task_service.create_task(db_session, user_id=test_user.id, title="Thing")
    task_service.complete_task(db_session, task.id, user_id=test_user.id)

    reopened = task_service.update_task(
        db_session, task.id, user_id=test_user.id, status=TaskStatus.TODO
    )

    assert reopened.completed_at is None


def test_completing_twice_keeps_the_original_time(
    db_session: Session, test_user: User
) -> None:
    task = task_service.create_task(db_session, user_id=test_user.id, title="Thing")
    first = task_service.complete_task(db_session, task.id, user_id=test_user.id)
    stamp = first.completed_at

    again = task_service.complete_task(db_session, task.id, user_id=test_user.id)

    assert again.completed_at == stamp


def test_an_overdue_task_stays_pending_until_somebody_completes_it(
    db_session: Session, test_user: User
) -> None:
    """The rule stated most explicitly in the milestone."""

    task = task_service.create_task(
        db_session,
        user_id=test_user.id,
        title="Late",
        due_at=datetime.now(UTC) - timedelta(days=5),
    )

    assert task.status is TaskStatus.TODO
    assert task_service.classify(task).is_overdue is True
    assert task.completed_at is None


# --- deduplication -------------------------------------------------------


def test_the_same_source_produces_one_task_however_often_it_is_seen(
    db_session: Session, test_user: User
) -> None:
    """Triage runs repeatedly over one inbox; the list must not grow with it."""

    key = "outlook:message:AAA"

    first, created_first = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Reply to Bob",
        source="email_follow_up",
    )
    second, created_second = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Reply to Bob",
        source="email_follow_up",
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert len(task_service.list_tasks(db_session, user_id=test_user.id)) == 1


def test_re_running_triage_does_not_reopen_a_completed_task(
    db_session: Session, test_user: User
) -> None:
    """The failure that would make the feature untrustworthy.

    Somebody completes a follow-up; triage runs again over the same message.
    If that reopened the task, the list would never stay done.
    """

    key = "outlook:message:BBB"
    task, _ = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Chase the quote",
        source="email_follow_up",
    )
    task_service.complete_task(db_session, task.id, user_id=test_user.id)

    again, created = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Chase the quote",
        source="email_follow_up",
    )

    assert created is False
    assert again.status is TaskStatus.COMPLETED


def test_re_running_triage_does_not_move_a_deadline_somebody_adjusted(
    db_session: Session, test_user: User
) -> None:
    key = "outlook:message:CCC"
    chosen = datetime(2026, 12, 25, 9, 0, tzinfo=UTC)

    task, _ = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Chase",
        source="email_follow_up",
        due_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    task_service.update_task(db_session, task.id, user_id=test_user.id, due_at=chosen)

    again, _ = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Chase",
        source="email_follow_up",
        due_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert again.due_at.astimezone(UTC) == chosen


def test_two_people_may_each_hold_a_task_from_the_same_message(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """Deduplication is per person — a broadcast reaches several inboxes."""

    key = "outlook:message:DDD"

    for user in (test_user, test_user_b):
        task_service.record_from_source(
            db_session,
            user_id=user.id,
            source_key=key,
            title="Respond to the all-hands note",
            source="email_follow_up",
        )

    assert len(task_service.list_tasks(db_session, user_id=test_user.id)) == 1
    assert len(task_service.list_tasks(db_session, user_id=test_user_b.id)) == 1


def test_late_arriving_detail_fills_gaps_without_overwriting(
    db_session: Session, test_user: User
) -> None:
    key = "outlook:message:EEE"

    task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Chase",
        source="email_follow_up",
        contact_address="bob@example.com",
    )
    updated, _ = task_service.record_from_source(
        db_session,
        user_id=test_user.id,
        source_key=key,
        title="Chase",
        source="email_follow_up",
        contact_address="someone-else@example.com",
        contact_name="Bob Keenan",
    )

    # The name was missing and is filled in; the address was already known and
    # is left alone.
    assert updated.contact_name == "Bob Keenan"
    assert updated.contact_address == "bob@example.com"


# --- ordering ------------------------------------------------------------


def test_tasks_come_back_soonest_first_with_undated_last(
    db_session: Session, test_user: User
) -> None:
    now = datetime.now(UTC)

    task_service.create_task(db_session, user_id=test_user.id, title="No date")
    task_service.create_task(
        db_session, user_id=test_user.id, title="Later", due_at=now + timedelta(days=9)
    )
    task_service.create_task(
        db_session, user_id=test_user.id, title="Sooner", due_at=now + timedelta(days=1)
    )

    titles = [
        task.title for task in task_service.list_tasks(db_session, user_id=test_user.id)
    ]

    assert titles == ["Sooner", "Later", "No date"]


def test_filtering_by_status_returns_only_that_status(
    db_session: Session, test_user: User
) -> None:
    task = task_service.create_task(db_session, user_id=test_user.id, title="One")
    task_service.create_task(db_session, user_id=test_user.id, title="Two")
    task_service.complete_task(db_session, task.id, user_id=test_user.id)

    open_tasks = task_service.list_tasks(
        db_session, user_id=test_user.id, statuses=[TaskStatus.TODO]
    )

    assert [item.title for item in open_tasks] == ["Two"]


def test_priority_defaults_to_normal(db_session: Session, test_user: User) -> None:
    task = task_service.create_task(db_session, user_id=test_user.id, title="Thing")

    assert task.priority is TaskPriority.NORMAL
