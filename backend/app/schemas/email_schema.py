"""HTTP contracts for the Email Agent.

One rule shapes every request model here: **no `user_id` field anywhere**. The
owner of a template, a draft or an assessment comes from `X-User-ID` through
the `CurrentUser` dependency, and a request body has no way to name somebody
else. That is not politeness, it is the multi-user boundary — a writable
`user_id` in a body would make every scoping check in the service layer
decorative.

Response models mirror the stored rows with two deliberate omissions:
attachment **bytes** are never returned (only filename, type and size), and
nothing carries a provider token. An attachment is downloaded through its own
endpoint or not at all.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.email import (
    EmailCategory,
    EmailDraftStatus,
    EmailPriority,
    EmailTemplateCategory,
)
from app.services.features.email.composer_service import EmailOperation

MAX_SUBJECT = 500
MAX_BODY = 50_000
MAX_RECIPIENTS = 100
MAX_INSTRUCTION = 4000


def _clean_list(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None

    return [item.strip() for item in value if item and item.strip()]


# --- templates -----------------------------------------------------------


class TemplateCreateRequest(BaseModel):
    """A new reusable template. Placeholders are written `{{ like_this }}`."""

    name: str = Field(min_length=1, max_length=255)
    subject_template: str = Field(max_length=MAX_SUBJECT)
    body_template: str = Field(min_length=1, max_length=MAX_BODY)
    description: str | None = Field(default=None, max_length=2000)
    category: EmailTemplateCategory = EmailTemplateCategory.CUSTOM

    @field_validator("name", "body_template")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """`min_length` alone would accept a string of spaces."""

        if not value.strip():
            raise ValueError("cannot be blank")

        return value


class TemplateUpdateRequest(BaseModel):
    """A partial change. Absent fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    subject_template: str | None = Field(default=None, max_length=MAX_SUBJECT)
    body_template: str | None = Field(default=None, min_length=1, max_length=MAX_BODY)
    description: str | None = Field(default=None, max_length=2000)
    category: EmailTemplateCategory | None = None

    @field_validator("name", "body_template")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("cannot be blank")

        return value

    def supplied(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class TemplateResponse(BaseModel):
    """A stored template, plus the placeholders it expects.

    `placeholders` is derived rather than stored: it is a fact about the text,
    and storing it would give two sources of truth that disagree the first time
    a template is edited.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    category: EmailTemplateCategory
    subject_template: str
    body_template: str
    placeholders: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TemplateListResponse(BaseModel):
    items: list[TemplateResponse]
    total: int


class TemplateFillRequest(BaseModel):
    """Values for a template's placeholders.

    An unsupplied placeholder stays visible as `{{ name }}` in the result
    rather than being blanked, so a person proof-reading can see what is still
    missing.
    """

    values: dict[str, str] = Field(default_factory=dict)


class TemplateFillResponse(BaseModel):
    subject: str
    body: str
    missing: list[str] = Field(
        default_factory=list,
        description="Placeholders that were left unfilled.",
    )


# --- drafts --------------------------------------------------------------


class AttachmentResponse(BaseModel):
    """An attachment, described. Bytes are never in a JSON response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class DraftCreateRequest(BaseModel):
    """A new draft. Everything is optional except that it has to be something.

    Recipients are not required here. A generated draft frequently has none
    yet, and refusing to save it would throw away the text while somebody looks
    up an address. They are required at approval, which is where the
    requirement actually matters.
    """

    to_recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    cc_recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    bcc_recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    subject: str = Field(default="", max_length=MAX_SUBJECT)
    body: str = Field(default="", max_length=MAX_BODY)
    template_id: uuid.UUID | None = None
    in_reply_to_message_id: str | None = Field(default=None, max_length=512)
    provider_thread_id: str | None = Field(default=None, max_length=512)
    generated_by_ai: bool = False

    @field_validator("to_recipients", "cc_recipients", "bcc_recipients")
    @classmethod
    def clean(cls, value: list[str]) -> list[str]:
        return _clean_list(value) or []


class DraftUpdateRequest(BaseModel):
    """A partial change to a draft.

    **Any change withdraws approval.** That is enforced in `draft_service`, not
    here, because it must hold however a draft is edited.
    """

    to_recipients: list[str] | None = Field(default=None, max_length=MAX_RECIPIENTS)
    cc_recipients: list[str] | None = Field(default=None, max_length=MAX_RECIPIENTS)
    bcc_recipients: list[str] | None = Field(default=None, max_length=MAX_RECIPIENTS)
    subject: str | None = Field(default=None, max_length=MAX_SUBJECT)
    body: str | None = Field(default=None, max_length=MAX_BODY)
    template_id: uuid.UUID | None = None
    in_reply_to_message_id: str | None = Field(default=None, max_length=512)

    @field_validator("to_recipients", "cc_recipients", "bcc_recipients")
    @classmethod
    def clean(cls, value: list[str] | None) -> list[str] | None:
        return _clean_list(value)

    def supplied(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class DraftResponse(BaseModel):
    """A stored draft.

    `sent_at` and `provider_message_id` are populated only by a real provider
    receipt. A draft showing `status="sent"` means a mailbox confirmed it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    to_recipients: list[str]
    cc_recipients: list[str]
    bcc_recipients: list[str]
    subject: str
    body: str
    status: EmailDraftStatus
    generated_by_ai: bool
    template_id: uuid.UUID | None
    in_reply_to_message_id: str | None
    provider: str | None
    provider_message_id: str | None
    provider_thread_id: str | None
    approved_at: datetime | None
    sent_at: datetime | None
    send_error: str | None
    attachments: list[AttachmentResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DraftListResponse(BaseModel):
    items: list[DraftResponse]
    total: int


class SendRequest(BaseModel):
    """The explicit act of sending.

    Deliberately carries a required `confirm`. The draft must *already* be
    approved — that check is in the service and cannot be bypassed from here —
    and this is the second, separate assertion that the person pressing send
    meant it. Two cheap gates on an irreversible action is the right number.
    """

    confirm: bool = Field(
        description=(
            "Must be true. Sending is irreversible and is never done on the "
            "assistant's own initiative."
        )
    )

    @field_validator("confirm")
    @classmethod
    def must_be_true(cls, value: bool) -> bool:
        if not value:
            raise ValueError("confirm must be true to send an email")

        return value


# --- generation ----------------------------------------------------------


class ComposeRequest(BaseModel):
    """One email operation.

    Operation-based rather than one endpoint per verb: "shorten" and "make more
    professional" differ by a sentence of instruction, and ten endpoints that
    differ by a sentence are ten things to keep in step.
    """

    operation: EmailOperation = EmailOperation.GENERATE
    instruction: str | None = Field(
        default=None,
        max_length=MAX_INSTRUCTION,
        description=(
            "What the email should do. Required for generate and reply; "
            "optional elsewhere, where it refines the operation."
        ),
    )
    subject: str = Field(default="", max_length=MAX_SUBJECT)
    body: str = Field(
        default="",
        max_length=MAX_BODY,
        description="The current draft. Required by every revision operation.",
    )
    tone: str | None = Field(default=None, max_length=255)
    recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)

    source_subject: str | None = Field(default=None, max_length=MAX_SUBJECT)
    source_body: str | None = Field(default=None, max_length=MAX_BODY)
    source_sender: str | None = Field(default=None, max_length=512)

    use_knowledge_base: bool = Field(
        default=False,
        description=(
            "Retrieve from the shared company knowledge base. When nothing "
            "relevant is found the model is told so explicitly and instructed "
            "to make no company claims; the response says which happened."
        ),
    )

    @field_validator("recipients")
    @classmethod
    def clean(cls, value: list[str]) -> list[str]:
        return _clean_list(value) or []


class ComposeSource(BaseModel):
    """A passage the model was shown while writing.

    Built from retrieval, never from the model's output — so, exactly as in
    chat, the Email Agent cannot cite a document that was not retrieved.
    """

    document: str
    section: str | None = None
    page: int | None = None
    similarity: float
    document_id: uuid.UUID
    chunk_id: uuid.UUID


class ComposeResponse(BaseModel):
    """Generated text, and an account of what produced it.

    Nothing here is saved. Creating a draft from this is a separate call, and
    sending it is two more — generate, save, approve, send.
    """

    subject: str
    body: str
    operation: EmailOperation
    sources: list[ComposeSource] = Field(default_factory=list)
    knowledge_used: bool = Field(
        description="False means no company knowledge reached the model."
    )
    persona_used: bool = Field(
        description="False means this user has no Digital Twin profile yet."
    )


# --- mailbox and triage --------------------------------------------------


class ProviderStatusResponse(BaseModel):
    """Whether a mailbox is connected, and what to do if not.

    `configured` and `connected` are separate on purpose: nothing set up is a
    setup step, and set-up-but-refused is a credentials or consent problem.
    """

    provider: str | None
    configured: bool
    connected: bool
    mailbox: str | None = None
    detail: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class MessageAddress(BaseModel):
    address: str
    name: str | None = None


class MessageAttachmentResponse(BaseModel):
    attachment_id: str
    filename: str
    content_type: str
    size_bytes: int


class MessageResponse(BaseModel):
    """One real message from the connected mailbox."""

    message_id: str
    thread_id: str | None = None
    sender: MessageAddress | None = None
    to_recipients: list[MessageAddress] = Field(default_factory=list)
    cc_recipients: list[MessageAddress] = Field(default_factory=list)
    subject: str = ""
    snippet: str = ""
    body: str | None = None
    received_at: datetime | None = None
    attachments: list[MessageAttachmentResponse] = Field(default_factory=list)
    folder: str | None = None
    labels: list[str] = Field(default_factory=list)
    is_read: bool | None = None


class AssessmentResponse(BaseModel):
    """What triage concluded about one message."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    provider_message_id: str
    provider_thread_id: str | None
    subject: str | None
    sender: str | None
    received_at: datetime | None
    category: EmailCategory
    priority: EmailPriority
    summary: str
    suggested_action: str | None
    action_items: list[str]
    follow_up_recommended: bool
    follow_up_reason: str | None
    follow_up_due_at: datetime | None
    handled: bool
    assessed_at: datetime


class TriagedMessageResponse(BaseModel):
    """A message paired with this user's assessment of it, if it has one.

    `assessment` is null for an untriaged message. Triage costs a model call
    per message, so it is asked for rather than run over an inbox on sight —
    and a null here means "not yet judged", never "judged unimportant".
    """

    message: MessageResponse
    assessment: AssessmentResponse | None = None


class InboxResponse(BaseModel):
    items: list[TriagedMessageResponse]
    total: int


class MessageInput(BaseModel):
    """A message supplied by the caller rather than fetched from a mailbox.

    This is what makes triage usable and testable before Outlook is connected:
    a caller that has a message from anywhere can have it assessed. Nothing
    about it is invented by this system.
    """

    message_id: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    sender: str | None = Field(default=None, max_length=512)
    to_recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    cc_recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    subject: str = Field(default="", max_length=MAX_SUBJECT)
    body: str = Field(default="", max_length=MAX_BODY)
    received_at: datetime | None = None


class TriageRequest(BaseModel):
    """Assess one message: either one already in the mailbox, or one supplied.

    Exactly one of `message_id` and `message` — a request naming both is
    ambiguous about which text was judged, and the answer would be attached to
    a message id it may not describe.
    """

    message_id: str | None = Field(
        default=None,
        max_length=512,
        description="Fetch this message from the connected mailbox and assess it.",
    )
    message: MessageInput | None = Field(
        default=None, description="Assess this message, supplied by the caller."
    )
    persist: bool = Field(
        default=True,
        description=(
            "Store the assessment so it appears in triage and follow-up "
            "lists. Set false to assess a message that is not in a mailbox."
        ),
    )


class ThreadSummaryRequest(BaseModel):
    """Summarise a conversation: one from the mailbox, or one supplied."""

    thread_id: str | None = Field(default=None, max_length=512)
    messages: list[MessageInput] = Field(default_factory=list, max_length=50)


class ThreadSummaryResponse(BaseModel):
    summary: str
    action_items: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    follow_up_recommended: bool = False
    follow_up_reason: str | None = None
    message_count: int


class AssessmentListResponse(BaseModel):
    items: list[AssessmentResponse]
    total: int


class HandledRequest(BaseModel):
    """Mark a follow-up dealt with, or put it back. Always a person's call."""

    handled: bool = True
