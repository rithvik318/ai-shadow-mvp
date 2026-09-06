"""Deleting a person, and everything that is theirs — and nothing that is not.

The dangerous operation in this codebase. Two owner concepts live side by side
and they are *not* the same kind of ownership:

**Private, and deleted here.** `digital_twin_profile`, `digital_twin_memory`,
`email_template`, `email_draft`, `email_attachment`, `email_assessment`,
`user_mailbox` and `task` all key on `users.id` — a UUID foreign key. Those
rows are one person's and mean nothing without them.

**Shared, and never touched.** `documents` and `document_chunks` carry a
`user_id` that is a `String(255)` holding the literal `MVP_USER_ID`
("mvp-user") for every upload. It is the *company* knowledge base, it has no
foreign key into `users`, and no user's id is ever written into it. Deleting a
person must not remove a single document, and `assert_shared_knowledge_intact`
plus the tests in `tests/services/features/users/test_deletion_service.py`
exist to make that failure loud rather than silent.

**Every row is deleted explicitly, in one transaction.** Not left to database
cascades: SQLite does not enforce foreign keys unless a pragma is set, the test
suite runs on SQLite, and a deletion that works in production but silently
orphans rows in tests is exactly the kind of thing that is discovered far too
late. Naming each table also makes the audit reviewable — a reader can see the
whole list without deriving it from constraint definitions.
"""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import UserNotFoundError
from app.models.calendar import CalendarEvent
from app.models.digital_twin import DigitalTwinMemory, DigitalTwinProfile
from app.models.document import Document, DocumentChunk
from app.models.email import (
    EmailAssessment,
    EmailAttachment,
    EmailDraft,
    EmailTemplate,
    UserMailbox,
)
from app.models.report import GeneratedReport
from app.models.task import Task
from app.models.user import User

logger = logging.getLogger(__name__)

# Deleted in this order: children before parents, so nothing is orphaned even
# where the database would not have enforced it.
_OWNED_MODELS = (
    # A person's stored reports are theirs. They contain their tasks, their
    # meetings and their correspondents, so they go when they do.
    GeneratedReport,
    CalendarEvent,
    Task,
    EmailAssessment,
    EmailTemplate,
    UserMailbox,
    DigitalTwinMemory,
    DigitalTwinProfile,
)

# Named so a reader can check the list against the schema without running it.
SHARED_TABLES = ("documents", "document_chunks")


@dataclass(frozen=True)
class DeletionSummary:
    """What was removed, per table.

    Returned rather than logged only, so the endpoint can report it and a test
    can assert on it. A deletion that reports nothing is a deletion nobody can
    verify.
    """

    user_id: uuid.UUID
    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


def shared_knowledge_counts(db: Session) -> tuple[int, int]:
    """How much shared knowledge exists right now.

    Used by the guard below and by tests. Counted rather than sampled: the
    property being protected is "none of it disappeared", and a sample cannot
    establish that.
    """

    documents = db.execute(select(func.count()).select_from(Document)).scalar_one()
    chunks = db.execute(select(func.count()).select_from(DocumentChunk)).scalar_one()

    return int(documents), int(chunks)


def assert_shared_knowledge_intact(db: Session, *, before: tuple[int, int]) -> None:
    """Refuse to finish a deletion that removed company knowledge.

    A belt-and-braces check inside the transaction. If a future change adds a
    cascade from `users` to `documents`, this raises and the transaction rolls
    back — rather than the knowledge base quietly shrinking every time somebody
    is offboarded.
    """

    after = shared_knowledge_counts(db)

    if after != before:
        raise RuntimeError(
            "Deleting a user removed shared knowledge base rows "
            f"(documents/chunks went from {before} to {after}). The knowledge "
            "base is shared company data and is never owned by one person. "
            "The transaction has been rolled back."
        )


def delete_user(db: Session, user_id: uuid.UUID) -> DeletionSummary:
    """Delete a user and every row that is theirs, in one transaction.

    Either all of it goes or none of it does. A partial deletion would leave a
    person's drafts and memories behind with no user to attribute them to, and
    no obvious way to find them again.
    """

    user = db.get(User, user_id)

    if user is None:
        raise UserNotFoundError(f"No user with id {user_id}.")

    before = shared_knowledge_counts(db)
    deleted: dict[str, int] = {}

    try:
        # Attachments hang off drafts, not off the user, so they are found
        # through their parent rather than by a user_id they do not have.
        draft_ids = list(
            db.execute(select(EmailDraft.id).where(EmailDraft.user_id == user_id))
            .scalars()
            .all()
        )

        if draft_ids:
            deleted["email_attachment"] = int(
                db.execute(
                    delete(EmailAttachment).where(
                        EmailAttachment.draft_id.in_(draft_ids)
                    )
                ).rowcount
                or 0
            )

        deleted["email_draft"] = int(
            db.execute(delete(EmailDraft).where(EmailDraft.user_id == user_id)).rowcount
            or 0
        )

        for model in _OWNED_MODELS:
            deleted[model.__tablename__] = int(
                db.execute(delete(model).where(model.user_id == user_id)).rowcount or 0
            )

        deleted["users"] = int(
            db.execute(delete(User).where(User.id == user_id)).rowcount or 0
        )

        assert_shared_knowledge_intact(db, before=before)

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("user_deletion_failed", extra={"user_id": str(user_id)})
        raise

    summary = DeletionSummary(user_id=user_id, deleted=deleted)

    logger.info(
        "user_deleted",
        extra={"user_id": str(user_id), "rows": summary.total},
    )

    return summary


def owned_row_counts(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    """What deleting this user would remove, per table.

    Read-only. Backs the confirmation dialog, so a person is told what they are
    about to destroy in numbers rather than in a generic warning — and so the
    dialog's list cannot drift away from what deletion actually does, because
    both are derived from the same tuple of models.
    """

    counts: dict[str, int] = {}

    draft_ids = list(
        db.execute(select(EmailDraft.id).where(EmailDraft.user_id == user_id))
        .scalars()
        .all()
    )

    counts["email_attachment"] = (
        int(
            db.execute(
                select(func.count())
                .select_from(EmailAttachment)
                .where(EmailAttachment.draft_id.in_(draft_ids))
            ).scalar_one()
        )
        if draft_ids
        else 0
    )
    counts["email_draft"] = len(draft_ids)

    for model in _OWNED_MODELS:
        counts[model.__tablename__] = int(
            db.execute(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            ).scalar_one()
        )

    return counts


def shared_knowledge_is_untouched_by(user_id: uuid.UUID) -> bool:
    """Whether this user's id could ever appear in the shared knowledge base.

    It cannot: those tables hold the literal `MVP_USER_ID` string, never a
    user UUID. Expressed as a function so the reasoning is executable rather
    than a comment somebody has to trust.
    """

    return str(user_id) != MVP_USER_ID
