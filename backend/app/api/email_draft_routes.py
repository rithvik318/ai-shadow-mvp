import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.database.session import get_db
from app.models.email import EmailDraftStatus
from app.schemas.document_schema import ErrorResponse
from app.schemas.email_schema import (
    AttachmentResponse,
    DraftCreateRequest,
    DraftListResponse,
    DraftResponse,
    DraftUpdateRequest,
    SendRequest,
)
from app.services.features.email import draft_service, sending_service

router = APIRouter(prefix="/email/drafts", tags=["email"])

IDENTITY_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "No X-User-ID header"},
    404: {"model": ErrorResponse, "description": "Unknown user or draft"},
    422: {"model": ErrorResponse, "description": "X-User-ID is not a UUID"},
}


@router.get(
    "",
    response_model=DraftListResponse,
    summary="List your drafts",
    responses=IDENTITY_RESPONSES,
)
def list_drafts(
    user: CurrentUser,
    db: Session = Depends(get_db),
    draft_status: EmailDraftStatus | None = Query(default=None, alias="status"),
) -> DraftListResponse:
    """This user's drafts, most recently changed first.

    Includes sent ones, so the workspace can show what went out. A draft is
    only ever `sent` because a provider confirmed it.
    """

    drafts = draft_service.list_drafts(db, status=draft_status, user_id=user.id)

    return DraftListResponse(
        items=[DraftResponse.model_validate(draft) for draft in drafts],
        total=len(drafts),
    )


@router.post(
    "",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a draft",
    responses={
        **IDENTITY_RESPONSES,
        422: {"model": ErrorResponse, "description": "Malformed recipient"},
    },
)
def create_draft(
    request: DraftCreateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DraftResponse:
    """Save an email in progress, generated or hand-written.

    Recipients are optional here and required at approval. A draft produced
    from an instruction often has none yet, and refusing to save it would
    discard the text while somebody looks up an address.
    """

    draft = draft_service.create_draft(
        db,
        to_recipients=request.to_recipients,
        cc_recipients=request.cc_recipients,
        bcc_recipients=request.bcc_recipients,
        subject=request.subject,
        body=request.body,
        template_id=request.template_id,
        in_reply_to_message_id=request.in_reply_to_message_id,
        provider_thread_id=request.provider_thread_id,
        generated_by_ai=request.generated_by_ai,
        status=(
            EmailDraftStatus.NEEDS_REVIEW
            if request.generated_by_ai
            else EmailDraftStatus.DRAFT
        ),
        user_id=user.id,
    )

    return DraftResponse.model_validate(draft)


@router.get(
    "/{draft_id}",
    response_model=DraftResponse,
    summary="Get one draft",
    responses=IDENTITY_RESPONSES,
)
def get_draft(
    draft_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DraftResponse:
    """Another user's draft is a `404`. Unsent mail is private."""

    return DraftResponse.model_validate(
        draft_service.get_draft(db, draft_id, user_id=user.id)
    )


@router.patch(
    "/{draft_id}",
    response_model=DraftResponse,
    summary="Update a draft",
    responses={
        **IDENTITY_RESPONSES,
        409: {"model": ErrorResponse, "description": "Already sent, or sending"},
        422: {"model": ErrorResponse, "description": "Malformed recipient"},
    },
)
def update_draft(
    draft_id: uuid.UUID,
    request: DraftUpdateRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DraftResponse:
    """Change the fields supplied.

    **Any edit withdraws approval** and returns the draft to `draft`. Approval
    means "send this text"; if the text can change afterwards it would mean
    "send whatever is here at send time", which is not a review. A sent draft
    cannot be edited at all — the message is gone, and changing the record of
    it would make the record wrong.
    """

    draft = draft_service.update_draft(
        db, draft_id, request.supplied(), user_id=user.id
    )

    return DraftResponse.model_validate(draft)


@router.delete(
    "/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft",
    responses=IDENTITY_RESPONSES,
)
def delete_draft(
    draft_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Response:
    """Delete a draft and its attachments. Deleting a sent one forgets the
    record; it does not unsend anything."""

    draft_service.delete_draft(db, draft_id, user_id=user.id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{draft_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a file to a draft",
    responses={
        **IDENTITY_RESPONSES,
        409: {"model": ErrorResponse, "description": "Too many attachments"},
        413: {"model": ErrorResponse, "description": "Attachment is too large"},
        422: {"model": ErrorResponse, "description": "Attachment is empty"},
    },
)
async def add_attachment(
    draft_id: uuid.UUID,
    user: CurrentUser,
    file: UploadFile = File(..., description="The file to attach"),
    db: Session = Depends(get_db),
) -> AttachmentResponse:
    """Attach a file.

    Any file type: an attachment is whatever a person means to send, and the
    ingestion pipeline's format list is about extracting text, which has
    nothing to do with this. Size and count are bounded by
    `EMAIL_MAX_ATTACHMENT_BYTES` and `EMAIL_MAX_ATTACHMENTS_PER_DRAFT`.

    Attaching changes the email, so it withdraws approval like any other edit.
    """

    data = await file.read()

    attachment = draft_service.add_attachment(
        db,
        draft_id,
        filename=file.filename or "attachment",
        content_type=file.content_type,
        data=data,
        user_id=user.id,
    )

    return AttachmentResponse.model_validate(attachment)


@router.delete(
    "/{draft_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an attachment",
    responses=IDENTITY_RESPONSES,
)
def remove_attachment(
    draft_id: uuid.UUID,
    attachment_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Response:
    """Detach a file. Also withdraws approval."""

    draft_service.remove_attachment(db, draft_id, attachment_id, user_id=user.id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{draft_id}/approve",
    response_model=DraftResponse,
    summary="Approve a draft for sending",
    responses={
        **IDENTITY_RESPONSES,
        409: {"model": ErrorResponse, "description": "Already sent, or sending"},
        422: {
            "model": ErrorResponse,
            "description": "No recipients, no subject or no body",
        },
    },
)
def approve_draft(
    draft_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DraftResponse:
    """Record that a person read this draft and wants it sent.

    **Approving does not send.** It is a separate, recorded act, and the send
    endpoint refuses anything that has not been through it — so approving and
    sending are two decisions rather than one button that does both. Editing
    the draft afterwards withdraws the approval.
    """

    return DraftResponse.model_validate(
        draft_service.approve_draft(db, draft_id, user_id=user.id)
    )


@router.post(
    "/{draft_id}/send",
    response_model=DraftResponse,
    summary="Send an approved draft through the connected mailbox",
    responses={
        **IDENTITY_RESPONSES,
        409: {
            "model": ErrorResponse,
            "description": ("Not approved, already sent, or no mailbox is connected"),
        },
        502: {"model": ErrorResponse, "description": "The provider refused"},
    },
)
def send_draft(
    draft_id: uuid.UUID,
    request: SendRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DraftResponse:
    """Send a draft a person has already approved.

    The full workflow is deliberate and cannot be short-circuited:

        generate → save → review → approve → send → confirmed or failed

    A draft that has not been approved is `409`, whether or not a mailbox is
    connected. With no mailbox connected, sending is `409` and nothing is
    marked sent — there is no simulated provider anywhere in this system, so a
    draft showing `sent` always means a real mailbox confirmed it.

    A provider failure sets `status="failed"` with the reason in `send_error`,
    and withdraws the approval: the next attempt is a new decision.
    """

    draft = sending_service.send_draft(db, draft_id, user_id=user.id)

    return DraftResponse.model_validate(draft)
