"""Reusable email templates, owned by one person each.

CRUD plus one pure function, `render`. Every query filters on `user_id`, and
`user_id` is a required keyword argument with no default — the same rule the
Digital Twin services follow, for the same reason: a default owner is a global
store that one forgotten argument reaches.

Placeholders are `{{ name }}`, substituted textually. Deliberately not
`str.format`: a template is written by a person in a text box, and "we charge
{rate} per hour" should produce that sentence rather than a `KeyError` — and
`str.format` on user-supplied text can also reach attributes of whatever is
passed to it, which is a needless thing to have to reason about.
"""

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    DuplicateEmailTemplateError,
    EmailTemplateNotFoundError,
)
from app.models.email import EmailTemplate, EmailTemplateCategory

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = (
    "name",
    "description",
    "category",
    "subject_template",
    "body_template",
)

# `{{ name }}`, `{{name}}`, `{{ first_name }}`. Anchored to word characters so
# a stray `{{` in prose does not become a placeholder with a nonsense name.
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def placeholders(*texts: str) -> list[str]:
    """Every distinct placeholder across the given texts, in first-seen order.

    Order matters because the UI renders one input per placeholder, and a form
    whose fields reshuffle between renders is a form people mistype.
    """

    seen: list[str] = []

    for text in texts:
        for match in PLACEHOLDER_PATTERN.finditer(text or ""):
            name = match.group(1)
            if name not in seen:
                seen.append(name)

    return seen


def render(text: str, values: dict[str, str]) -> str:
    """Substitute the placeholders a caller supplied, and leave the rest alone.

    An unfilled placeholder stays visibly `{{ like_this }}` rather than being
    blanked. A person proof-reading a draft can see what they forgot; an empty
    space where a client's name should be is the mistake that actually gets
    sent.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name)

        return match.group(0) if value is None else str(value)

    return PLACEHOLDER_PATTERN.sub(replace, text or "")


def create_template(
    db: Session,
    *,
    name: str,
    subject_template: str,
    body_template: str,
    description: str | None = None,
    category: EmailTemplateCategory = EmailTemplateCategory.CUSTOM,
    user_id: uuid.UUID,
) -> EmailTemplate:
    """Store a template for this user, or raise if they already have that name.

    Uniqueness is the database's, not a prior SELECT — two concurrent requests
    both find nothing and both insert, and only the constraint catches it. The
    same reasoning `user_service.create_user` documents.
    """

    template = EmailTemplate(
        user_id=user_id,
        name=name.strip(),
        description=(description.strip() if description else None),
        category=category,
        subject_template=subject_template,
        body_template=body_template,
    )
    db.add(template)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateEmailTemplateError(
            f"You already have a template called {name!r}."
        ) from exc

    db.refresh(template)

    logger.info(
        "email_template_created",
        extra={
            "user_id": str(user_id),
            "template_id": str(template.id),
            "category": template.category.value,
        },
    )

    return template


def list_templates(
    db: Session,
    *,
    category: EmailTemplateCategory | None = None,
    user_id: uuid.UUID,
) -> list[EmailTemplate]:
    """This user's templates, alphabetically, so the list is stable."""

    predicates = [EmailTemplate.user_id == user_id]

    if category is not None:
        predicates.append(EmailTemplate.category == category)

    return list(
        db.execute(
            select(EmailTemplate)
            .where(*predicates)
            .order_by(EmailTemplate.name, EmailTemplate.id)
        )
        .scalars()
        .all()
    )


def get_template(
    db: Session, template_id: uuid.UUID, *, user_id: uuid.UUID
) -> EmailTemplate:
    """Return this user's template, or raise.

    Another user's template is missing, not forbidden. A 403 would confirm that
    it exists, which is a fact about somebody else's workspace.
    """

    template = db.execute(
        select(EmailTemplate).where(
            EmailTemplate.id == template_id,
            EmailTemplate.user_id == user_id,
        )
    ).scalar_one_or_none()

    if template is None:
        raise EmailTemplateNotFoundError(f"Email template not found: {template_id}")

    return template


def update_template(
    db: Session,
    template_id: uuid.UUID,
    values: dict[str, object],
    *,
    user_id: uuid.UUID,
) -> EmailTemplate:
    """Change the fields the caller supplied, and only those."""

    template = get_template(db, template_id, user_id=user_id)

    for field, value in values.items():
        if field in EDITABLE_FIELDS:
            setattr(template, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateEmailTemplateError(
            "You already have a template with that name."
        ) from exc

    db.refresh(template)

    return template


def delete_template(db: Session, template_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Remove a template. Drafts started from it are unaffected.

    The foreign key is `ON DELETE SET NULL`, so a draft loses the record of
    where it came from and keeps everything a person actually wrote.
    """

    template = get_template(db, template_id, user_id=user_id)
    db.delete(template)
    db.commit()

    logger.info(
        "email_template_deleted",
        extra={"user_id": str(user_id), "template_id": str(template_id)},
    )
