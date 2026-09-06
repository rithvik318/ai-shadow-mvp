"""What the Email Agent stores: templates, drafts, attachments, assessments.

Four tables, and the line between them is which question they answer.

A **template** is a reusable shape a person wrote — subject and body with
placeholders. A **draft** is one specific email in progress, owned by the
person who will send it. An **attachment** is bytes belonging to a draft. An
**assessment** is what triage concluded about a message that *already exists in
somebody's mailbox*.

Nothing here is a mailbox. There is no `email_message` table, and there never
should be: mirroring a mailbox into this database would duplicate a system of
record, and the moment it drifted the UI would be showing mail that is not
there. Messages are read from the provider on demand, and only what triage
*concluded* is kept — plus the subject, sender and timestamp needed to identify
the row to a human. Message bodies are not stored.

Provider identifiers are nullable strings, never foreign keys and never parsed.
`provider` names which system a `provider_message_id` belongs to, so the same
column can hold an Outlook id today and a Gmail one later without a migration
and without either provider's format leaking into the schema.

Everything is owned by a `User`, the same way the Digital Twin is. Drafts and
templates are private: a person's unsent mail is not company knowledge.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import UtcDateTime

# Lists of short strings — recipient addresses, action items. JSONB on
# Postgres so they are queryable if that is ever needed; plain JSON elsewhere,
# which is what the SQLite test suite creates. Same construct the Digital Twin
# profile uses, for the same reason.
StringListType = JSON().with_variant(JSONB, "postgresql")


class EmailDraftStatus(StrEnum):
    """Where a draft stands on its way out.

    The order is the human-in-the-loop workflow, and the states exist to make
    each step refusable rather than to decorate a list:

    - `draft` — being written. Editable. Cannot be sent.
    - `needs_review` — generation finished; a person has not read it yet.
    - `approved` — a person read it and said send. **Editing returns it to
      `draft`**, so an approved draft is always the text that was approved.
    - `sending` — handed to the provider; the outcome is not yet known.
    - `sent` — a provider confirmed it. Written nowhere else, by nothing else.
    - `failed` — the provider refused or never answered. `send_error` says
      what happened, and the draft can be corrected and approved again.
    """

    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class EmailCategory(StrEnum):
    """What triage decided a message is."""

    URGENT = "urgent"
    NEEDS_REPLY = "needs_reply"
    FYI = "fyi"
    FOLLOW_UP = "follow_up"
    LOW_PRIORITY = "low_priority"


class EmailPriority(StrEnum):
    """How soon a message wants attention, independent of what it is.

    Separate from the category because the two genuinely differ: an FYI from a
    regulator is high priority and needs no reply, and collapsing them would
    lose one of those facts.
    """

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class EmailTemplateCategory(StrEnum):
    """What a template is for. A label, and nothing branches on it.

    `custom` is the default and the escape hatch, so the list never has to grow
    to accommodate a template nobody anticipated.
    """

    INTRODUCTION = "introduction"
    FOLLOW_UP = "follow_up"
    MEETING_REQUEST = "meeting_request"
    PROPOSAL_FOLLOW_UP = "proposal_follow_up"
    THANK_YOU = "thank_you"
    OUTREACH = "outreach"
    CUSTOM = "custom"


def _enum_column(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """A varchar plus a CHECK constraint, storing the member *value*.

    SQLAlchemy persists a PEP-435 enum by member name unless told otherwise,
    which would write `NEEDS_REPLY` while the migration's constraint expects
    `needs_reply`. Same treatment `DocumentStatus` and `MemoryType` get.
    """

    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
    )


EmailDraftStatusType = _enum_column(EmailDraftStatus, "email_draft_status")
EmailCategoryType = _enum_column(EmailCategory, "email_category")
EmailPriorityType = _enum_column(EmailPriority, "email_priority")
EmailTemplateCategoryType = _enum_column(
    EmailTemplateCategory, "email_template_category"
)


class EmailTemplate(Base):
    """A reusable subject and body, owned by one person.

    User-owned rather than global, deliberately. Every other per-person thing
    in this system — profile, memory — belongs to a user, and a shared template
    library would need an ownership and permission model that does not exist
    yet. A template worth sharing can be copied; a global table cannot be
    un-shared.

    Placeholders are written `{{ name }}` and substituted by
    `template_service.render`. Not `str.format`: a template is written by a
    person, and a stray brace in "we charge {rate} per hour" should not be able
    to raise or to reach into an object.
    """

    __tablename__ = "email_template"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[EmailTemplateCategory] = mapped_column(
        EmailTemplateCategoryType,
        nullable=False,
        default=EmailTemplateCategory.CUSTOM,
    )

    subject_template: Mapped[str] = mapped_column(Text, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # Unique per owner, not globally: two people may both have a template
        # called "Follow-up", and neither should have to rename theirs.
        UniqueConstraint("user_id", "name", name="uq_email_template_user_name"),
    )


class EmailDraft(Base):
    """One email in progress, owned by the person who would send it."""

    __tablename__ = "email_draft"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Which mailbox system the identifiers below belong to. NULL until a
    # provider has touched this draft — most drafts never do, because a draft
    # is composed locally and only meets a provider at send time.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set once the provider confirms a send. Before that there is no message.
    provider_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # A draft created *in* the provider's own drafts folder, if that path is
    # ever used. Distinct from `provider_message_id`, which means "sent".
    provider_draft_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # The message this is a reply to, when it is one. A provider id, so the
    # provider can thread the reply correctly rather than guessing from
    # subject lines.
    in_reply_to_message_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )

    to_recipients: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )
    cc_recipients: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )
    bcc_recipients: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )

    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[EmailDraftStatus] = mapped_column(
        EmailDraftStatusType,
        nullable=False,
        default=EmailDraftStatus.DRAFT,
        index=True,
    )
    # Whether a model wrote any of this. Shown in the UI, because a person
    # reviewing text should know whether they or a model produced it.
    generated_by_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        # SET NULL, not CASCADE: deleting the template a draft started from
        # must not delete the draft. The link is provenance, not ownership.
        ForeignKey("email_template.id", ondelete="SET NULL"),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    send_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    attachments: Mapped[list["EmailAttachment"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="EmailAttachment.created_at",
    )

    __table_args__ = (Index("ix_email_draft_user_status", "user_id", "status"),)


class EmailAttachment(Base):
    """A file attached to a draft, bytes and all.

    The bytes live in the row. That is a deliberate, bounded choice rather than
    a storage design: `EMAIL_MAX_ATTACHMENT_BYTES` and
    `EMAIL_MAX_ATTACHMENTS_PER_DRAFT` cap what a draft can hold, and an
    attachment's whole life is "uploaded, then handed to a provider at send
    time". Introducing an object store for that would be a second storage
    system, a second failure mode and a second thing to garbage-collect, for a
    payload this system already refuses to let grow. When attachments outgrow
    it, `content` becomes a key and this docstring is the note explaining why
    it was not one from the start — see docs/KNOWN_ISSUES.md.
    """

    __tablename__ = "email_attachment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("email_draft.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )

    draft: Mapped["EmailDraft"] = relationship(back_populates="attachments")


class EmailAssessment(Base):
    """What triage concluded about one message in somebody's mailbox.

    A cache of a judgement, not a copy of the mail. Rows appear only when a
    real message is assessed, so an unconnected deployment has none and the
    inbox view has nothing to show — which is the correct thing for it to show.

    Kept rather than recomputed because assessment costs a model call, and
    re-running it every time an inbox is opened would be a bill and a delay for
    an answer that has not changed.

    `handled` is the follow-up half: the system may recommend following someone
    up, and a person marks that dealt with. Nothing acts on it automatically.
    """

    __tablename__ = "email_assessment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_thread_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Headers, not content. Enough for a person to recognise which message a
    # row is about; the body is read from the provider when it is needed.
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kept apart deliberately. A display name is for a person to read; an
    # address is what a reply is actually sent to. Storing them joined as
    # "Robert Keenan <Robert.Keenan@sunradia.com>" forced every consumer that
    # wanted to reply to parse a display string back into an address, and the
    # frontend was one such consumer. The provider carries both fields; this
    # is where that structure stops being thrown away.
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    category: Mapped[EmailCategory] = mapped_column(EmailCategoryType, nullable=False)
    priority: Mapped[EmailPriority] = mapped_column(EmailPriorityType, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_items: Mapped[list[str]] = mapped_column(
        StringListType, nullable=False, default=list
    )

    follow_up_recommended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    follow_up_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Only set when the message itself implies a date. Never a date this
    # system chose — a made-up deadline is worse than none.
    follow_up_due_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    handled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    assessed_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # One assessment per message per person. Re-assessing updates the row
        # rather than adding a second opinion nobody asked for.
        UniqueConstraint(
            "user_id",
            "provider",
            "provider_message_id",
            name="uq_email_assessment_message",
        ),
        Index(
            "ix_email_assessment_follow_up",
            "user_id",
            "follow_up_recommended",
            "handled",
        ),
        Index(
            "ix_email_assessment_sender_address",
            "user_id",
            "sender_address",
        ),
    )

    @property
    def sender_display(self) -> str | None:
        """How to show the sender to a person — never what to send mail to.

        The one place the joined form is produced. It exists so that no caller
        has to build it by hand and then be tempted to reuse the result as an
        address, which is precisely the defect this pair of columns replaced.
        """

        if self.sender_name and self.sender_address:
            return f"{self.sender_name} <{self.sender_address}>"

        return self.sender_address or self.sender_name


class UserMailbox(Base):
    """Which mailbox one person's Email Agent acts on.

    This is the row that replaced `EMAIL_MAILBOX_ADDRESS`. That setting made
    the mailbox a property of the deployment, so every user of a server shared
    one inbox and one sending address, and changing whose it was meant editing
    the environment and restarting.

    **No credentials live here.** Graph application permissions carry no user:
    the client id and secret authenticate the *application*, and the only thing
    that varies per person is which mailbox that application is asked to act
    on. Storing a secret per user would be inventing a second authentication
    system, which the milestone explicitly rules out.

    `provider` is stored rather than assumed so that one person can be on
    Outlook while another is on whatever is added next, without a migration.
    """

    __tablename__ = "user_mailbox"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Unique, not merely indexed: one mailbox per person is a rule, and a
    # constraint is the only form of it the database can enforce.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("user_id", name="uq_user_mailbox_user"),)
