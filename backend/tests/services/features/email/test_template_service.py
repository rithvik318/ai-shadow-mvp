"""Templates: CRUD, placeholder handling, and the boundary between two users."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    DuplicateEmailTemplateError,
    EmailTemplateNotFoundError,
)
from app.models.email import EmailTemplateCategory
from app.models.user import User
from app.services.features.email import template_service


def _create(db: Session, user: User, **overrides: object) -> object:
    values: dict = {
        "name": "Follow-up",
        "subject_template": "Following up on {{ topic }}",
        "body_template": "Hi {{ name }},\n\nAbout {{ topic }} — any thoughts?",
    }
    values.update(overrides)

    return template_service.create_template(db, user_id=user.id, **values)


def test_a_template_is_stored_with_its_owner(
    db_session: Session, test_user: User
) -> None:
    template = _create(db_session, test_user)

    assert template.user_id == test_user.id
    assert template.category is EmailTemplateCategory.CUSTOM


def test_two_users_may_hold_the_same_template_name(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """Uniqueness is per owner. Two people both having a "Follow-up" template
    is the normal case, and forcing one of them to rename theirs because a
    stranger got there first would be absurd."""

    _create(db_session, test_user)
    _create(db_session, test_user_b)

    assert len(template_service.list_templates(db_session, user_id=test_user.id)) == 1
    assert len(template_service.list_templates(db_session, user_id=test_user_b.id)) == 1


def test_one_user_may_not_reuse_a_name(db_session: Session, test_user: User) -> None:
    _create(db_session, test_user)

    with pytest.raises(DuplicateEmailTemplateError):
        _create(db_session, test_user)


def test_another_users_template_is_invisible(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """The isolation that matters: not a filtered list, but an unreachable row."""

    template = _create(db_session, test_user)

    assert template_service.list_templates(db_session, user_id=test_user_b.id) == []

    with pytest.raises(EmailTemplateNotFoundError):
        template_service.get_template(db_session, template.id, user_id=test_user_b.id)


def test_another_users_template_cannot_be_edited(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    template = _create(db_session, test_user)

    with pytest.raises(EmailTemplateNotFoundError):
        template_service.update_template(
            db_session, template.id, {"name": "Hijacked"}, user_id=test_user_b.id
        )

    db_session.refresh(template)
    assert template.name == "Follow-up"


def test_another_users_template_cannot_be_deleted(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    template = _create(db_session, test_user)

    with pytest.raises(EmailTemplateNotFoundError):
        template_service.delete_template(
            db_session, template.id, user_id=test_user_b.id
        )

    assert template_service.get_template(db_session, template.id, user_id=test_user.id)


def test_a_missing_template_is_not_found(db_session: Session, test_user: User) -> None:
    with pytest.raises(EmailTemplateNotFoundError):
        template_service.get_template(db_session, uuid.uuid4(), user_id=test_user.id)


def test_update_changes_only_what_was_supplied(
    db_session: Session, test_user: User
) -> None:
    template = _create(db_session, test_user)

    updated = template_service.update_template(
        db_session,
        template.id,
        {"description": "For lapsed prospects"},
        user_id=test_user.id,
    )

    assert updated.description == "For lapsed prospects"
    assert updated.subject_template == "Following up on {{ topic }}"


def test_update_ignores_fields_that_are_not_editable(
    db_session: Session, test_user: User, test_user_b: User
) -> None:
    """A `user_id` in a values dict must not move a template between owners."""

    template = _create(db_session, test_user)

    template_service.update_template(
        db_session,
        template.id,
        {"user_id": test_user_b.id, "name": "Renamed"},
        user_id=test_user.id,
    )
    db_session.refresh(template)

    assert template.user_id == test_user.id
    assert template.name == "Renamed"


def test_deleting_a_template_removes_it(db_session: Session, test_user: User) -> None:
    template = _create(db_session, test_user)

    template_service.delete_template(db_session, template.id, user_id=test_user.id)

    assert template_service.list_templates(db_session, user_id=test_user.id) == []


def test_templates_can_be_filtered_by_category(
    db_session: Session, test_user: User
) -> None:
    _create(
        db_session, test_user, name="Intro", category=EmailTemplateCategory.INTRODUCTION
    )
    _create(
        db_session, test_user, name="Thanks", category=EmailTemplateCategory.THANK_YOU
    )

    found = template_service.list_templates(
        db_session, category=EmailTemplateCategory.INTRODUCTION, user_id=test_user.id
    )

    assert [item.name for item in found] == ["Intro"]


# --- placeholders and rendering ------------------------------------------


def test_placeholders_are_listed_in_first_seen_order() -> None:
    """Order is stable because the UI renders one input per placeholder, and a
    form whose fields reshuffle between renders is one people mistype."""

    found = template_service.placeholders(
        "Following up on {{ topic }}", "Hi {{ name }}, about {{ topic }}"
    )

    assert found == ["topic", "name"]


def test_placeholders_tolerate_spacing() -> None:
    assert template_service.placeholders("{{name}} and {{  name  }}") == ["name"]


def test_render_substitutes_supplied_values() -> None:
    result = template_service.render(
        "Hi {{ name }}, about {{ topic }}", {"name": "Ana", "topic": "the pilot"}
    )

    assert result == "Hi Ana, about the pilot"


def test_render_leaves_unfilled_placeholders_visible() -> None:
    """Blanking them is the failure mode that actually gets sent: an empty
    space where a client's name should be reads as finished text."""

    result = template_service.render(
        "Hi {{ name }}, about {{ topic }}", {"name": "Ana"}
    )

    assert result == "Hi Ana, about {{ topic }}"
    assert template_service.placeholders(result) == ["topic"]


def test_render_does_not_treat_single_braces_as_placeholders() -> None:
    """A template is written by a person in a text box. `str.format` would
    raise on this, or worse, resolve it against something."""

    text = "We charge {rate} per hour and use {0} for billing."

    assert template_service.render(text, {"rate": "200"}) == text
