"""The mailbox boundary: what every provider must offer, in nobody's dialect.

This module is the whole reason connecting Outlook later is a configuration
change rather than a rewrite. Everything above it — composing, triage,
follow-ups, the API, the UI — is written against the types declared here, and
none of them contains a Microsoft word. Everything below it translates one
vendor's shape into these types and nothing else.

Two rules keep that boundary real, and both are worth stating because both are
easy to erode one convenience at a time:

**Nothing here imports from `app.services.features`, `app.models` or
`app.schemas`.** A provider must not know what a draft is, who a user is, or
what the database stores. It knows addresses, messages and bytes.

**No provider ever fabricates a send.** `send()` either returns a `SendReceipt`
carrying an identifier the provider itself issued, or raises. There is no code
path in this package that returns success without a provider having said so,
and there is no in-memory or "demo" provider that pretends — because the moment
one exists, a UI showing "Sent" stops meaning anything.

Dataclasses rather than Pydantic models on purpose: these are the internal
currency between two layers, not a validated HTTP contract. The schemas layer
converts them at the edge, which is where request validation belongs.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmailAddress:
    """One mailbox, and the name attached to it where the provider gives one."""

    address: str
    name: str | None = None

    def __str__(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass(frozen=True)
class AttachmentRef:
    """An attachment on a received message, described but not downloaded.

    Bytes are deliberately absent. Listing an inbox would otherwise download
    every attachment on every message to render a paperclip icon. `content` is
    fetched on demand through `EmailProvider.get_attachment`.
    """

    attachment_id: str
    filename: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class EmailMessage:
    """One message in a mailbox, in provider-neutral form.

    `thread_id` is whatever the provider calls a conversation. It is opaque:
    nothing above this layer parses it, and two providers' thread ids are never
    compared.

    `body` may be absent when the message came from a listing rather than a
    fetch — providers return a preview for lists and the full body only when
    asked. `snippet` is always populated where the provider offers one, so a
    triage view has something to show without a fetch per row.
    """

    message_id: str
    thread_id: str | None = None
    sender: EmailAddress | None = None
    to_recipients: list[EmailAddress] = field(default_factory=list)
    cc_recipients: list[EmailAddress] = field(default_factory=list)
    subject: str = ""
    snippet: str = ""
    body: str | None = None
    received_at: datetime | None = None
    attachments: list[AttachmentRef] = field(default_factory=list)
    # Folder and labels are the same idea under two vendors' names — Outlook
    # has folders, Gmail has labels, and a message can be in one folder and
    # carry several labels. Both are carried so neither has to be faked.
    folder: str | None = None
    labels: list[str] = field(default_factory=list)
    is_read: bool | None = None

    @property
    def text(self) -> str:
        """The most complete text available, for a model to read."""

        return self.body if self.body is not None else self.snippet


@dataclass(frozen=True)
class OutgoingAttachment:
    """A file to attach to an outgoing message, bytes included."""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class OutgoingEmail:
    """An email ready to hand to a provider.

    Constructed only by the sending service, and only from a draft a person has
    explicitly approved. The provider does not check that — approval is a
    domain concept and belongs above this layer — but nothing else in the
    codebase builds one of these.
    """

    to_recipients: list[str]
    subject: str
    body: str
    cc_recipients: list[str] = field(default_factory=list)
    bcc_recipients: list[str] = field(default_factory=list)
    attachments: list[OutgoingAttachment] = field(default_factory=list)
    # When set, the provider is expected to thread the message as a reply to
    # this one rather than starting a new conversation.
    in_reply_to_message_id: str | None = None


@dataclass(frozen=True)
class SendReceipt:
    """Proof, from the provider, that a message left.

    Returned only by a provider that actually sent something. `message_id` may
    be None where a provider's send API does not return one — Graph's
    `sendMail` is exactly that case — and that is honest: the message went, and
    this system cannot name it. It is never filled in with a guess.
    """

    provider: str
    sent_at: datetime
    message_id: str | None = None
    thread_id: str | None = None


@dataclass(frozen=True)
class ProviderStatus:
    """What to tell a person about the mailbox connection.

    `configured` and `connected` are separate because they fail differently:
    nothing configured is a setup step, and configured-but-refused is a
    credential or consent problem. Collapsing them would send somebody to the
    wrong page.

    `detail` is written for a person and never carries a token, a secret or a
    stack trace.
    """

    provider: str | None
    configured: bool
    connected: bool
    mailbox: str | None = None
    detail: str | None = None
    capabilities: list[str] = field(default_factory=list)


@runtime_checkable
class EmailProvider(Protocol):
    """What the Email Agent needs from a mailbox.

    A Protocol rather than a base class: a provider is defined by what it can
    do, and inheritance would buy nothing but an import from every
    implementation back to this module. Tests substitute a provider by
    implementing this surface, not by patching a global.

    Every method may raise `EmailProviderNotConfiguredError`,
    `EmailProviderAuthError` or `EmailSendError` from `app.core.exceptions`.
    Those three are the only failures callers above are expected to handle, and
    each implementation is responsible for translating its vendor's errors into
    them rather than letting an `httpx` exception escape.
    """

    @property
    def name(self) -> str:
        """Short stable identifier stored alongside provider ids, e.g.
        `outlook`."""

    def status(self) -> ProviderStatus:
        """Describe the connection without raising.

        The one method that must never raise: it exists so a UI can say "not
        connected" instead of showing an error page on every load.
        """

    def list_messages(
        self, *, limit: int = 25, folder: str | None = None
    ) -> list[EmailMessage]:
        """Recent messages, newest first."""

    def get_message(self, message_id: str) -> EmailMessage:
        """One message, with its body."""

    def get_thread(self, thread_id: str) -> list[EmailMessage]:
        """Every message in a conversation, oldest first."""

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """The bytes of one attachment on a received message."""

    def create_draft(self, email: OutgoingEmail) -> str:
        """Store a draft in the provider's own drafts folder, returning its id.

        Optional in practice: this system keeps its drafts locally so that
        composing works with no mailbox at all. This exists for the deployment
        that wants drafts visible in Outlook before they are sent.
        """

    def send(self, email: OutgoingEmail) -> SendReceipt:
        """Send, or raise. Never returns a receipt for a message that did not
        go."""
