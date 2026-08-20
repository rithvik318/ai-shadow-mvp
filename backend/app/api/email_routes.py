import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.core.exceptions import EmailValidationError
from app.database.session import get_db
from app.models.email import EmailAssessment, EmailCategory
from app.schemas.document_schema import ErrorResponse
from app.schemas.email_schema import (
    AssessmentListResponse,
    AssessmentResponse,
    ComposeRequest,
    ComposeResponse,
    ComposeSource,
    HandledRequest,
    InboxResponse,
    MessageAddress,
    MessageAttachmentResponse,
    MessageInput,
    MessageResponse,
    ProviderStatusResponse,
    ThreadSummaryRequest,
    ThreadSummaryResponse,
    TriagedMessageResponse,
    TriageRequest,
)
from app.services.email.provider import registry
from app.services.email.provider.base import EmailAddress, EmailMessage
from app.services.features.email import (
    composer_service,
    mailbox_service,
    triage_service,
)

router = APIRouter(prefix="/email", tags=["email"])

IDENTITY_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "No X-User-ID header"},
    404: {"model": ErrorResponse, "description": "Unknown user"},
    422: {"model": ErrorResponse, "description": "X-User-ID is not a UUID"},
}

MAILBOX_RESPONSES: dict[int | str, dict] = {
    **IDENTITY_RESPONSES,
    409: {"model": ErrorResponse, "description": "No mailbox is connected"},
    502: {"model": ErrorResponse, "description": "The mailbox provider failed"},
}


def _to_message_response(message: EmailMessage) -> MessageResponse:
    def address(item: EmailAddress) -> MessageAddress:
        return MessageAddress(address=item.address, name=item.name)

    return MessageResponse(
        message_id=message.message_id,
        thread_id=message.thread_id,
        sender=address(message.sender) if message.sender else None,
        to_recipients=[address(item) for item in message.to_recipients],
        cc_recipients=[address(item) for item in message.cc_recipients],
        subject=message.subject,
        snippet=message.snippet,
        body=message.body,
        received_at=message.received_at,
        attachments=[
            MessageAttachmentResponse(
                attachment_id=item.attachment_id,
                filename=item.filename,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
            )
            for item in message.attachments
        ],
        folder=message.folder,
        labels=message.labels,
        is_read=message.is_read,
    )


def _to_email_message(supplied: MessageInput) -> EmailMessage:
    """Turn a caller-supplied message into the provider-neutral type.

    The same type a provider produces, so triage cannot tell the difference —
    which is exactly why triage is testable, and usable, before Outlook is
    connected.
    """

    return EmailMessage(
        message_id=supplied.message_id,
        thread_id=supplied.thread_id,
        sender=(EmailAddress(address=supplied.sender) if supplied.sender else None),
        to_recipients=[EmailAddress(address=item) for item in supplied.to_recipients],
        cc_recipients=[EmailAddress(address=item) for item in supplied.cc_recipients],
        subject=supplied.subject,
        snippet=supplied.body[:255],
        body=supplied.body,
        received_at=supplied.received_at,
    )


# --- provider ------------------------------------------------------------


@router.get(
    "/provider/status",
    response_model=ProviderStatusResponse,
    summary="Whether a mailbox is connected",
)
def provider_status() -> ProviderStatusResponse:
    """Report the mailbox connection. Never fails, by design.

    A deployment with no mailbox is a normal state, not an error: drafting,
    rewriting, templates and saved drafts all work without one. This endpoint
    is what lets the UI say so plainly instead of showing invented mail.
    """

    state = registry.status()

    return ProviderStatusResponse(
        provider=state.provider,
        configured=state.configured,
        connected=state.connected,
        mailbox=state.mailbox,
        detail=state.detail,
        capabilities=state.capabilities,
    )


# --- generation ----------------------------------------------------------


@router.post(
    "/compose",
    response_model=ComposeResponse,
    summary="Generate or revise email text with the Digital Twin",
    responses={
        **IDENTITY_RESPONSES,
        422: {"model": ErrorResponse, "description": "The operation needs more input"},
        502: {
            "model": ErrorResponse,
            "description": "The language or embedding provider failed",
        },
    },
)
def compose(
    request: ComposeRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ComposeResponse:
    """Run one email operation and return the text.

    Operation-based rather than an endpoint per verb: `generate`, `reply`,
    `rewrite`, `improve`, `shorten`, `expand`, `change_tone`, `professional`,
    `concise` and `subject` differ by a sentence of instruction.

    The Digital Twin of the user named by `X-User-ID` shapes the writing — role,
    priorities, communication style and active memories. Two people asking for
    the same email from the same knowledge base get different drafts, and
    nobody else's twin is ever loaded.

    With `use_knowledge_base`, the shared company corpus is searched and the
    retrieved passages are put in front of the model. When nothing relevant is
    found, the model is told so explicitly and instructed to make no company
    claims at all — `knowledge_used` reports which happened, and `sources` is
    built from retrieval rather than from the model's output, so a source
    cannot be fabricated.

    **Nothing is saved and nothing is sent.** Turning this into a draft is a
    separate call, and sending it takes two more.
    """

    composed = composer_service.compose(
        db,
        operation=request.operation,
        instruction=request.instruction,
        subject=request.subject,
        body=request.body,
        tone=request.tone,
        recipients=request.recipients,
        source_subject=request.source_subject,
        source_body=request.source_body,
        source_sender=request.source_sender,
        use_knowledge_base=request.use_knowledge_base,
        twin_user_id=user.id,
    )

    return ComposeResponse(
        subject=composed.subject,
        body=composed.body,
        operation=composed.operation,
        sources=[
            ComposeSource(
                document=chunk.filename,
                section=chunk.section_title,
                page=chunk.page_number,
                similarity=chunk.similarity,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
            )
            for chunk in composed.sources
        ],
        knowledge_used=composed.knowledge_used,
        persona_used=composed.persona_used,
    )


# --- mailbox -------------------------------------------------------------


@router.get(
    "/messages",
    response_model=InboxResponse,
    summary="Recent messages from the connected mailbox",
    responses=MAILBOX_RESPONSES,
)
def list_messages(
    user: CurrentUser,
    db: Session = Depends(get_db),
    limit: int = Query(default=25, ge=1, le=100),
    folder: str | None = Query(default=None),
) -> InboxResponse:
    """Real messages, newest first, each with this user's assessment if it has
    one.

    `409` when no mailbox is connected, rather than an empty list: "no mail" and
    "no mailbox" are different facts, and a person seeing an empty inbox should
    not have to guess which one they are looking at.

    `assessment` is null for a message nobody has triaged. Triage costs a model
    call per message, so it is requested per message rather than run over an
    inbox on sight — a null means "not yet judged", never "judged unimportant".
    """

    triaged = mailbox_service.list_inbox(
        db, user_id=user.id, limit=limit, folder=folder
    )

    return InboxResponse(
        items=[
            TriagedMessageResponse(
                message=_to_message_response(item.message),
                assessment=(
                    AssessmentResponse.model_validate(item.assessment)
                    if item.assessment
                    else None
                ),
            )
            for item in triaged
        ],
        total=len(triaged),
    )


@router.get(
    "/messages/{message_id}",
    response_model=TriagedMessageResponse,
    summary="One message, with its body",
    responses=MAILBOX_RESPONSES,
)
def get_message(
    message_id: str,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TriagedMessageResponse:
    """Fetch one message from the connected mailbox."""

    item = mailbox_service.get_message(db, message_id, user_id=user.id)

    return TriagedMessageResponse(
        message=_to_message_response(item.message),
        assessment=(
            AssessmentResponse.model_validate(item.assessment)
            if item.assessment
            else None
        ),
    )


# --- triage --------------------------------------------------------------


@router.post(
    "/triage",
    response_model=AssessmentResponse,
    summary="Classify and summarise one message",
    responses={
        **MAILBOX_RESPONSES,
        422: {"model": ErrorResponse, "description": "No message, or an empty one"},
    },
)
def triage(
    request: TriageRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> AssessmentResponse:
    """Assess one message: category, priority, summary, action items and
    whether it needs following up.

    Two ways in. `message_id` fetches from the connected mailbox. `message`
    supplies one directly, which is what makes triage usable before Outlook is
    connected — a caller with a message from anywhere can have it assessed, and
    nothing about it is invented by this system.

    Judged against this user's Digital Twin, so what counts as urgent follows
    their responsibilities and priorities rather than a generic notion of
    importance.

    Stored by default, keyed by the provider's own message id, so an inbox does
    not pay for a model call per row every time it is opened. Re-assessing
    updates the same row. Pass `persist: false` for a message that is not in a
    mailbox and should not appear in follow-up lists.
    """

    if bool(request.message_id) == bool(request.message):
        raise EmailValidationError(
            "Send either message_id, to assess a message from the connected "
            "mailbox, or message, to assess one you supply — not both."
        )

    if request.message_id:
        item = mailbox_service.get_message(db, request.message_id, user_id=user.id)
        message = item.message
        provider = registry.get_provider().name
    else:
        message = _to_email_message(request.message)
        # Attributed to the configured provider where there is one, so a
        # supplied message and the same message fetched later are one row
        # rather than two. "manual" only where no provider is configured at all.
        provider = registry.configured_provider_name() or "manual"

    assessment = triage_service.assess_message(
        db,
        message,
        provider=provider,
        user_id=user.id,
        persist=request.persist,
    )

    return AssessmentResponse.model_validate(assessment)


@router.post(
    "/threads/summarize",
    response_model=ThreadSummaryResponse,
    summary="Summarise an email thread",
    responses={
        **MAILBOX_RESPONSES,
        422: {"model": ErrorResponse, "description": "No messages to summarise"},
    },
)
def summarize_thread(
    request: ThreadSummaryRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ThreadSummaryResponse:
    """Summarise a conversation: what was decided, what is open, who owes what.

    Either `thread_id`, read from the connected mailbox, or `messages` supplied
    directly. Not stored: a thread grows, and a saved summary of one is wrong
    as soon as somebody replies.
    """

    if bool(request.thread_id) == bool(request.messages):
        raise EmailValidationError(
            "Send either thread_id or messages — not both, and not neither."
        )

    if request.thread_id:
        messages = mailbox_service.get_thread(request.thread_id)
    else:
        messages = [_to_email_message(item) for item in request.messages]

    summary = triage_service.summarize_thread(db, messages, user_id=user.id)

    return ThreadSummaryResponse(
        summary=summary.summary,
        action_items=summary.action_items,
        suggested_action=summary.suggested_action,
        follow_up_recommended=summary.follow_up_recommended,
        follow_up_reason=summary.follow_up_reason,
        message_count=len(messages),
    )


@router.get(
    "/assessments",
    response_model=AssessmentListResponse,
    summary="Every message you have had triaged",
    responses=IDENTITY_RESPONSES,
)
def list_assessments(
    user: CurrentUser,
    db: Session = Depends(get_db),
    category: EmailCategory | None = Query(default=None),
) -> AssessmentListResponse:
    """Stored assessments for this user, most recent message first.

    Reads the database only — no mailbox is contacted, so this works while the
    provider is unreachable. An unconnected deployment has none, because
    nothing here is ever invented.
    """

    rows = triage_service.list_assessments(db, category=category, user_id=user.id)

    return _assessment_list(rows)


# --- follow-ups ----------------------------------------------------------


@router.get(
    "/follow-ups",
    response_model=AssessmentListResponse,
    summary="Messages that may need following up",
    responses=IDENTITY_RESPONSES,
)
def list_follow_ups(
    user: CurrentUser,
    db: Session = Depends(get_db),
    include_handled: bool = Query(default=False),
) -> AssessmentListResponse:
    """Follow-ups triage recommended, soonest due first.

    **Recommendations only.** Nothing here is scheduled, nothing is sent, and
    nothing is marked handled on a person's behalf. Drafting the follow-up is
    `POST /email/compose`; sending it is the ordinary approve-then-send path.

    Rows with no due date sort last: a dated commitment is the one that can be
    missed, and an undated "should circle back" should not push it down.
    """

    rows = triage_service.list_follow_ups(
        db, include_handled=include_handled, user_id=user.id
    )

    return _assessment_list(rows)


@router.post(
    "/follow-ups/{assessment_id}/handled",
    response_model=AssessmentResponse,
    summary="Mark a follow-up handled, or put it back",
    responses={
        **IDENTITY_RESPONSES,
        422: {"model": ErrorResponse, "description": "Unknown assessment"},
    },
)
def set_handled(
    assessment_id: uuid.UUID,
    request: HandledRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> AssessmentResponse:
    """A person's call, always. Nothing marks itself handled."""

    assessment = triage_service.set_handled(
        db, assessment_id, handled=request.handled, user_id=user.id
    )

    return AssessmentResponse.model_validate(assessment)


def _assessment_list(rows: list[EmailAssessment]) -> AssessmentListResponse:
    return AssessmentListResponse(
        items=[AssessmentResponse.model_validate(row) for row in rows],
        total=len(rows),
    )
