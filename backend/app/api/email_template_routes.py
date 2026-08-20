import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.database.session import get_db
from app.models.email import EmailTemplate, EmailTemplateCategory
from app.schemas.document_schema import ErrorResponse
from app.schemas.email_schema import (
    TemplateCreateRequest,
    TemplateFillRequest,
    TemplateFillResponse,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdateRequest,
)
from app.services.features.email import template_service

router = APIRouter(prefix="/email/templates", tags=["email"])

IDENTITY_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "No X-User-ID header"},
    404: {"model": ErrorResponse, "description": "Unknown user"},
    422: {"model": ErrorResponse, "description": "X-User-ID is not a UUID"},
}


def _response(template: EmailTemplate) -> TemplateResponse:
    """Add the derived placeholder list to the stored row.

    Derived on read rather than stored: it is a fact about the template text,
    and a stored copy would disagree with it the first time somebody edits the
    body without going through this service.
    """

    return TemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        category=template.category,
        subject_template=template.subject_template,
        body_template=template.body_template,
        placeholders=template_service.placeholders(
            template.subject_template, template.body_template
        ),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.get(
    "",
    response_model=TemplateListResponse,
    summary="List your email templates",
    responses=IDENTITY_RESPONSES,
)
def list_templates(
    user: CurrentUser,
    db: Session = Depends(get_db),
    category: EmailTemplateCategory | None = Query(default=None),
) -> TemplateListResponse:
    """This user's templates, alphabetically.

    Templates are private to the person who wrote them. Another user's
    templates are not in this list under any filter.
    """

    templates = template_service.list_templates(db, category=category, user_id=user.id)

    return TemplateListResponse(
        items=[_response(template) for template in templates],
        total=len(templates),
    )


@router.post(
    "",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an email template",
    responses={
        **IDENTITY_RESPONSES,
        409: {"model": ErrorResponse, "description": "You already have that name"},
    },
)
def create_template(
    request: TemplateCreateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Store a reusable template, owned by the current user.

    Placeholders are written `{{ name }}`. The owner comes from `X-User-ID`;
    the request body has no `user_id` field, so a body cannot file a template
    under somebody else.
    """

    template = template_service.create_template(
        db,
        name=request.name,
        subject_template=request.subject_template,
        body_template=request.body_template,
        description=request.description,
        category=request.category,
        user_id=user.id,
    )

    return _response(template)


@router.get(
    "/{template_id}",
    response_model=TemplateResponse,
    summary="Get one template",
    responses={**IDENTITY_RESPONSES, 404: IDENTITY_RESPONSES[404]},
)
def get_template(
    template_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Another user's template is a `404`, exactly as an unknown id is."""

    return _response(template_service.get_template(db, template_id, user_id=user.id))


@router.patch(
    "/{template_id}",
    response_model=TemplateResponse,
    summary="Update a template",
    responses={
        **IDENTITY_RESPONSES,
        409: {"model": ErrorResponse, "description": "You already have that name"},
    },
)
def update_template(
    template_id: uuid.UUID,
    request: TemplateUpdateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Change the fields supplied, and leave the rest alone."""

    template = template_service.update_template(
        db, template_id, request.supplied(), user_id=user.id
    )

    return _response(template)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a template",
    responses=IDENTITY_RESPONSES,
)
def delete_template(
    template_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Response:
    """Delete a template. Drafts written from it are untouched — they keep
    everything a person actually wrote, and simply lose the record of where
    they started."""

    template_service.delete_template(db, template_id, user_id=user.id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{template_id}/fill",
    response_model=TemplateFillResponse,
    summary="Fill a template's placeholders",
    responses=IDENTITY_RESPONSES,
)
def fill_template(
    template_id: uuid.UUID,
    request: TemplateFillRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TemplateFillResponse:
    """Substitute the values supplied and return the resulting text.

    No model is called: this is textual substitution, and paying for a
    completion to replace `{{ name }}` with a name would be slower, dearer and
    less predictable than doing it directly. Generating *from* a filled
    template is a separate `POST /email/compose` with the result as the body.

    Placeholders left unfilled stay visible in the output and are listed in
    `missing`, so a person can see what they still have to supply rather than
    finding a blank where a client's name should be.
    """

    template = template_service.get_template(db, template_id, user_id=user.id)

    subject = template_service.render(template.subject_template, request.values)
    body = template_service.render(template.body_template, request.values)

    return TemplateFillResponse(
        subject=subject,
        body=body,
        missing=template_service.placeholders(subject, body),
    )
