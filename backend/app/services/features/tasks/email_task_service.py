"""Turning a triage follow-up into a task, without ever turning it into two.

Kept separate from `triage_service` on purpose. Triage's job is to *judge* a
message; creating work is a different concern, and wiring it into the assessment
path would mean every re-assessment carried a side effect on somebody's task
list.

The whole difficulty here is idempotence. Triage is re-run over the same inbox
whenever somebody opens it, and a naive implementation would add a task per run
until the list was unusable. `source_key` is the identity that prevents that:
one message, one task, however many times it is judged.

Nothing here decides that work is *finished*. A follow-up that triage stops
recommending — because the thread moved on, or because a later judgement read
it differently — does not complete or delete the task. Only a person does that.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.models.email import EmailAssessment, EmailPriority
from app.services.features.tasks import task_service
from app.services.features.tasks.urgency import TaskPriority

logger = logging.getLogger(__name__)

SOURCE_FOLLOW_UP = "email_follow_up"

# Triage's priority scale and the task scale are deliberately separate types —
# one describes a message, the other a piece of work — so the mapping is
# explicit rather than a shared enum that would couple the two.
# Triage has three levels, tasks have four. `urgent` has no email equivalent
# and is deliberately unreachable from here: it is reserved for a person
# marking something urgent themselves, and a model deciding that on its own
# would make the top of the scale meaningless.
_PRIORITY = {
    EmailPriority.HIGH: TaskPriority.HIGH,
    EmailPriority.NORMAL: TaskPriority.NORMAL,
    EmailPriority.LOW: TaskPriority.LOW,
}


def source_key_for(assessment: EmailAssessment) -> str:
    """The stable identity of "the task this message produced".

    Built from the provider and the provider's own message id, so it survives
    re-assessment, a replaced assessment row, and a restart. Deliberately not
    the assessment's primary key: re-triaging can replace that row, and the
    task must not become a second one when it does.
    """

    return f"{assessment.provider}:{assessment.provider_message_id}:follow_up"


def task_title_for(assessment: EmailAssessment) -> str:
    """A title a person can recognise in a list, without the body.

    Uses the subject, because that is what the message is called in their
    mailbox. Falls back to the sender, then to a bare statement — never to a
    summary the model wrote, which would read as though the system had decided
    what the work is.
    """

    if assessment.subject and assessment.subject.strip():
        return f"Follow up: {assessment.subject.strip()}"

    if assessment.sender_name or assessment.sender_address:
        return f"Follow up with {assessment.sender_name or assessment.sender_address}"

    return "Follow up on a message"


def sync_follow_up_task(
    db: Session, assessment: EmailAssessment, *, user_id: uuid.UUID
) -> tuple[object | None, bool]:
    """Create the task for this follow-up if there is not one already.

    Returns `(task, created)`, or `(None, False)` when triage did not recommend
    a follow-up — in which case **no existing task is touched**. A judgement
    that changed its mind is not evidence that the work was done.
    """

    if not assessment.follow_up_recommended:
        return None, False

    task, created = task_service.record_from_source(
        db,
        user_id=user_id,
        source_key=source_key_for(assessment),
        title=task_title_for(assessment),
        source=SOURCE_FOLLOW_UP,
        description=assessment.follow_up_reason or assessment.summary,
        priority=_PRIORITY.get(assessment.priority, TaskPriority.NORMAL),
        # Only a date the *message* implied. Triage never invents one, and this
        # passes through whatever it found — including nothing.
        due_at=assessment.follow_up_due_at,
        source_provider=assessment.provider,
        source_message_id=assessment.provider_message_id,
        source_thread_id=assessment.provider_thread_id,
        source_assessment_id=assessment.id,
        # Structured, so a drafted follow-up is addressed to an address rather
        # than to "Robert Keenan <Robert.Keenan@sunradia.com>".
        contact_name=assessment.sender_name,
        contact_address=assessment.sender_address,
    )

    if created:
        logger.info(
            "follow_up_task_created",
            extra={"user_id": str(user_id), "provider": assessment.provider},
        )

    return task, created
