"""Deleting a person removes everything of theirs, and no company knowledge.

The second half of that sentence is the one that matters. `documents` and
`document_chunks` carry a `user_id` column, it is a string, and it holds
`MVP_USER_ID` for everybody — so a change that treated it like the Digital
Twin's `user_id` would delete the shared knowledge base every time somebody was
offboarded, and would look correct while doing it.

Several tests here therefore assert an *absence of effect*, which is unusual
and deliberate.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import UserNotFoundError
from app.models.digital_twin import DigitalTwinMemory, DigitalTwinProfile, MemoryType
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.email import (
    EmailAssessment,
    EmailCategory,
    EmailPriority,
    EmailTemplate,
    EmailTemplateCategory,
)
from app.models.task import Task
from app.models.user import User
from app.services.features.tasks import task_service
from app.services.features.users import deletion_service


def _shared_document(db: Session, *, filename: str) -> Document:
    """A knowledge base document — owned by the company, not by a person."""

    document = Document(
        user_id=MVP_USER_ID,
        filename=filename,
        content_type="text/plain",
        file_size_bytes=10,
        status=DocumentStatus.INDEXED,
        content_hash=uuid.uuid4().hex,
    )
    db.add(document)
    db.flush()

    db.add(
        DocumentChunk(
            document_id=document.id,
            user_id=MVP_USER_ID,
            chunk_index=0,
            content="Shared company knowledge.",
            char_count=25,
        )
    )
    db.commit()

    return document


def _populate(db: Session, user: User) -> None:
    """Give this user one row in every table they can own."""

    db.add(
        DigitalTwinProfile(
            user_id=user.id,
            name=user.name,
            role="Director",
            organization="Sun Radia",
        )
    )
    db.add(
        DigitalTwinMemory(
            user_id=user.id, type=MemoryType.FACT, content="Remembers things"
        )
    )
    db.add(
        EmailTemplate(
            user_id=user.id,
            name="Intro",
            category=EmailTemplateCategory.OUTREACH,
            subject_template="Hello",
            body_template="Hi there",
        )
    )
    db.add(
        EmailAssessment(
            user_id=user.id,
            provider="outlook",
            provider_message_id="m1",
            category=EmailCategory.NEEDS_REPLY,
            priority=EmailPriority.HIGH,
            summary="Needs an answer",
            sender_name="Robert Keenan",
            sender_address="Robert.Keenan@sunradia.com",
        )
    )
    db.commit()

    task_service.create_task(db, user_id=user.id, title="A task")


# --- the shared knowledge base -------------------------------------------


def test_deleting_a_user_removes_no_knowledge_base_documents(
    db_session: Session, test_user: User
) -> None:
    """The single most important assertion in this module."""

    _shared_document(db_session, filename="capabilities.pdf")
    _shared_document(db_session, filename="case-study.pdf")
    _populate(db_session, test_user)

    before = deletion_service.shared_knowledge_counts(db_session)
    deletion_service.delete_user(db_session, test_user.id)

    assert deletion_service.shared_knowledge_counts(db_session) == before
    assert before == (2, 2)


def test_knowledge_base_chunks_survive_too(
    db_session: Session, test_user: User
) -> None:
    """Chunks carry their own denormalised `user_id`, so they need their own check."""

    _shared_document(db_session, filename="whitepaper.pdf")
    _populate(db_session, test_user)

    deletion_service.delete_user(db_session, test_user.id)

    assert db_session.query(DocumentChunk).count() == 1


def test_a_user_id_is_never_the_shared_knowledge_owner(test_user: User) -> None:
    """The reasoning behind the guard, made executable.

    A user's id is a UUID; the knowledge base's owner is the literal string
    "mvp-user". They cannot collide, which is why deleting by `user_id` can
    never reach a document.
    """

    assert deletion_service.shared_knowledge_is_untouched_by(test_user.id)
    assert str(test_user.id) != MVP_USER_ID


def test_the_guard_would_catch_an_accidental_cascade(
    db_session: Session, test_user: User
) -> None:
    """If a future change did start deleting documents, this must be loud.

    Simulated by telling the guard the knowledge base was larger before than it
    is now — the same signal a real accidental cascade would produce.
    """

    _shared_document(db_session, filename="one.pdf")

    with pytest.raises(RuntimeError, match="shared knowledge"):
        deletion_service.assert_shared_knowledge_intact(db_session, before=(99, 99))


# --- what is removed -----------------------------------------------------


def test_every_owned_table_is_emptied(db_session: Session, test_user: User) -> None:
    _populate(db_session, test_user)
    user_id = test_user.id

    deletion_service.delete_user(db_session, user_id)

    assert db_session.query(User).filter(User.id == user_id).count() == 0
    assert (
        db_session.query(DigitalTwinProfile)
        .filter(DigitalTwinProfile.user_id == user_id)
        .count()
        == 0
    )
    assert (
        db_session.query(DigitalTwinMemory)
        .filter(DigitalTwinMemory.user_id == user_id)
        .count()
        == 0
    )
    assert (
        db_session.query(EmailTemplate).filter(EmailTemplate.user_id == user_id).count()
        == 0
    )
    assert (
        db_session.query(EmailAssessment)
        .filter(EmailAssessment.user_id == user_id)
        .count()
        == 0
    )
    assert db_session.query(Task).filter(Task.user_id == user_id).count() == 0


def test_the_summary_reports_what_it_removed(
    db_session: Session, test_user: User
) -> None:
    _populate(db_session, test_user)

    summary = deletion_service.delete_user(db_session, test_user.id)

    assert summary.deleted["users"] == 1
    assert summary.deleted["task"] == 1
    assert summary.deleted["digital_twin_memory"] == 1
    assert summary.total >= 5


def test_the_preview_matches_what_deletion_removes(
    db_session: Session, test_user: User
) -> None:
    """The dialog's numbers and the deletion's numbers come from one source."""

    _populate(db_session, test_user)

    preview = deletion_service.owned_row_counts(db_session, test_user.id)
    summary = deletion_service.delete_user(db_session, test_user.id)

    for table, count in preview.items():
        assert summary.deleted.get(table, 0) == count


def test_deleting_one_user_leaves_another_untouched(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    _populate(db_session, test_user)
    _populate(db_session, test_user_b)

    deletion_service.delete_user(db_session, test_user.id)

    assert db_session.query(User).filter(User.id == test_user_b.id).count() == 1
    assert db_session.query(Task).filter(Task.user_id == test_user_b.id).count() == 1
    assert (
        db_session.query(DigitalTwinMemory)
        .filter(DigitalTwinMemory.user_id == test_user_b.id)
        .count()
        == 1
    )


def test_deleting_a_user_who_does_not_exist_is_a_not_found(
    db_session: Session,
) -> None:
    with pytest.raises(UserNotFoundError):
        deletion_service.delete_user(db_session, uuid.uuid4())


def test_a_user_with_nothing_deletes_cleanly(
    db_session: Session, test_user: User
) -> None:
    summary = deletion_service.delete_user(db_session, test_user.id)

    assert summary.deleted["users"] == 1
    assert db_session.query(User).count() == 0


# --- events go with their owner ------------------------------------------


def test_a_deleted_users_events_go_with_them(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """A new user-owned table has to join `_OWNED_MODELS`, or its rows outlive
    the person they describe. This test is what makes forgetting that fail."""

    from datetime import UTC, datetime, timedelta

    from app.models.calendar import CalendarEvent
    from app.services.features.calendar import event_service

    mine = event_service.create_event(
        db_session,
        user_id=test_user.id,
        title="Mine",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )
    event_service.create_event(
        db_session,
        user_id=test_user_b.id,
        title="Theirs",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )

    # Read before the delete: afterwards the instance is detached and touching
    # any attribute raises rather than answering.
    mine_id = mine.id
    other_id = test_user_b.id

    summary = deletion_service.delete_user(db_session, user_id=test_user.id)

    assert summary.deleted["calendar_event"] == 1

    assert (
        db_session.execute(
            select(CalendarEvent).where(CalendarEvent.id == mine_id)
        ).scalar_one_or_none()
        is None
    )
    # The other person's week is untouched.
    assert len(event_service.list_events(db_session, user_id=other_id)) == 1
