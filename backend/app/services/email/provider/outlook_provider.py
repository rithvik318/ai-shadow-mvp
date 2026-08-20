"""Microsoft Graph as an `EmailProvider`.

Translation, and only translation: Graph's JSON in, the vendor-neutral types
from `base.py` out. There is no business logic here, no database, and no
knowledge of drafts, users or approval.

**No second authentication.** The token, the throttling retries, the paging and
the error translation all come from `app.services.graph.client.GraphClient`,
which OneDrive sync already uses. One Entra application, one token cache, one
place where a 403 is turned into a sentence. Note that this is a *sharing of
the application*, not of its consent: `Files.Read.All` does not grant mail
access, and `Mail.Read` / `Mail.Send` have to be granted to the same
application before anything here works. That is what
`status().detail` is for.

**Application permissions carry no user.** A client-credentials token is the
application acting as itself, so every call has to name the mailbox it means —
hence `EMAIL_MAILBOX_ADDRESS`, and hence the deliberate refusal to guess one.

**Nothing here has been exercised against a live tenant.** The request shapes
follow Microsoft's documented `/users/{id}/messages`, `/sendMail` and
`/attachments` contracts and the response mapping is tested against recorded
payloads, but no SunRadia mailbox has been connected. Until one is, the honest
description of this module is "integration-ready", and `docs/FEATURES.md` says
exactly that.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from app.config.settings import settings
from app.core.exceptions import (
    EmailProviderAuthError,
    EmailProviderNotConfiguredError,
    EmailSendError,
    GraphAuthError,
    GraphError,
    SyncNotConfiguredError,
)
from app.services.email.provider.base import (
    AttachmentRef,
    EmailAddress,
    EmailMessage,
    OutgoingEmail,
    ProviderStatus,
    SendReceipt,
)
from app.services.graph.client import GraphClient

logger = logging.getLogger(__name__)

PROVIDER_NAME = "outlook"

# Asked for explicitly so a listing costs one round trip and returns a preview
# rather than every body. `body` is fetched only by `get_message`.
_LIST_FIELDS = (
    "id,conversationId,subject,bodyPreview,from,toRecipients,ccRecipients,"
    "receivedDateTime,hasAttachments,isRead,parentFolderId"
)


def _parse_datetime(value: str | None) -> datetime | None:
    """Graph's ISO 8601, or None.

    A timestamp that will not parse is dropped rather than raised on: the
    message is still perfectly usable without it, and refusing to list an inbox
    because one row had an odd date would be a poor trade.
    """

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("graph_mail_unparsable_timestamp")
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _address(payload: dict | None) -> EmailAddress | None:
    """Unwrap Graph's `{emailAddress: {name, address}}` shape."""

    if not payload:
        return None

    inner = payload.get("emailAddress") or {}
    address = inner.get("address")

    if not address:
        return None

    return EmailAddress(address=str(address), name=inner.get("name") or None)


def _addresses(payloads: list[dict] | None) -> list[EmailAddress]:
    return [address for address in map(_address, payloads or []) if address is not None]


def _body_text(payload: dict) -> str | None:
    """The message body as text, whatever content type Graph returned.

    HTML is passed through unconverted. Stripping tags here would be a second,
    worse parser than the one `app/services/features/documents` already owns,
    and the request asks for `text` — this is the fallback path for a mailbox
    that ignores it.
    """

    body = payload.get("body")

    if not isinstance(body, dict):
        return None

    content = body.get("content")

    return str(content) if content is not None else None


def _attachments(payloads: list[dict] | None) -> list[AttachmentRef]:
    refs: list[AttachmentRef] = []

    for payload in payloads or []:
        identifier = payload.get("id")

        if not identifier:
            continue

        refs.append(
            AttachmentRef(
                attachment_id=str(identifier),
                filename=str(payload.get("name") or "attachment"),
                content_type=str(
                    payload.get("contentType") or "application/octet-stream"
                ),
                size_bytes=int(payload.get("size") or 0),
            )
        )

    return refs


def to_message(payload: dict) -> EmailMessage:
    """Map one Graph `message` resource onto the neutral type.

    Module-level and public so the mapping can be tested against recorded Graph
    payloads without constructing a client or a transport. Every field is read
    defensively: Graph omits what was not requested, and a missing key is a
    normal response rather than a broken one.
    """

    return EmailMessage(
        message_id=str(payload.get("id") or ""),
        thread_id=(
            str(payload["conversationId"]) if payload.get("conversationId") else None
        ),
        sender=_address(payload.get("from") or payload.get("sender")),
        to_recipients=_addresses(payload.get("toRecipients")),
        cc_recipients=_addresses(payload.get("ccRecipients")),
        subject=str(payload.get("subject") or ""),
        snippet=str(payload.get("bodyPreview") or ""),
        body=_body_text(payload),
        received_at=_parse_datetime(payload.get("receivedDateTime")),
        attachments=_attachments(payload.get("attachments")),
        folder=(
            str(payload["parentFolderId"]) if payload.get("parentFolderId") else None
        ),
        labels=[str(item) for item in (payload.get("categories") or [])],
        is_read=payload.get("isRead")
        if isinstance(payload.get("isRead"), bool)
        else None,
    )


def _recipients(addresses: list[str]) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": address}} for address in addresses]


class OutlookEmailProvider:
    """Graph-backed mailbox access. Satisfies `EmailProvider` structurally."""

    def __init__(self, *, mailbox: str, client: GraphClient) -> None:
        self._mailbox = mailbox
        self._client = client

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def mailbox(self) -> str:
        return self._mailbox

    @classmethod
    def from_settings(
        cls, *, client: GraphClient | None = None
    ) -> "OutlookEmailProvider":
        """Build from configuration, or say precisely what is missing.

        `EMAIL_MAILBOX_ADDRESS` is checked here rather than deferred to the
        first call, because "which mailbox?" has no sensible default under
        application permissions and discovering that at send time would be the
        worst possible moment.
        """

        mailbox = settings.EMAIL_MAILBOX_ADDRESS

        if not mailbox:
            raise EmailProviderNotConfiguredError(
                "The Outlook provider needs EMAIL_MAILBOX_ADDRESS — the address "
                "of the mailbox to act on. Application permissions carry no "
                "user, so every mail call has to name one."
            )

        if client is None:
            try:
                client = GraphClient.from_settings()
            except SyncNotConfiguredError as exc:
                # Graph credentials are shared with OneDrive sync, so the
                # missing-variable message names ONEDRIVE_*. Re-raised as an
                # email error with that explained, rather than letting a sync
                # error surface from an email endpoint.
                raise EmailProviderNotConfiguredError(
                    f"Microsoft Graph is not configured. {exc} The Email Agent "
                    "shares the Entra application used for OneDrive "
                    "synchronisation."
                ) from exc

        return cls(mailbox=mailbox, client=client)

    # --- reading ---------------------------------------------------------

    def _base(self) -> str:
        return f"/users/{self._mailbox}"

    def status(self) -> ProviderStatus:
        """Describe the connection. Never raises — that is the point of it."""

        capabilities = ["list_messages", "get_message", "get_thread", "send", "reply"]

        try:
            client = GraphClient.from_settings()
        except SyncNotConfiguredError as exc:
            return ProviderStatus(
                provider=PROVIDER_NAME,
                configured=False,
                connected=False,
                mailbox=self._mailbox,
                detail=str(exc),
            )

        try:
            # One cheap call that proves the token mints *and* that mail
            # consent was granted. A folder read fails with 403 when
            # Mail.Read is missing, which is the failure most likely to be
            # sitting in wait — the OneDrive consent does not cover it.
            client.get(f"{self._base()}/mailFolders/inbox", {"$select": "id"})
        except GraphAuthError as exc:
            return ProviderStatus(
                provider=PROVIDER_NAME,
                configured=True,
                connected=False,
                mailbox=self._mailbox,
                detail=(
                    f"{exc} Mail access is a separate consent from files: the "
                    "application needs Mail.Read and Mail.Send granted."
                ),
            )
        except GraphError as exc:
            return ProviderStatus(
                provider=PROVIDER_NAME,
                configured=True,
                connected=False,
                mailbox=self._mailbox,
                detail=str(exc),
            )

        return ProviderStatus(
            provider=PROVIDER_NAME,
            configured=True,
            connected=True,
            mailbox=self._mailbox,
            capabilities=capabilities,
        )

    def list_messages(
        self, *, limit: int = 25, folder: str | None = None
    ) -> list[EmailMessage]:
        path = (
            f"{self._base()}/mailFolders/{folder}/messages"
            if folder
            else f"{self._base()}/messages"
        )

        payload = self._call(
            path,
            {
                "$top": max(1, min(limit, 100)),
                "$select": _LIST_FIELDS,
                "$orderby": "receivedDateTime desc",
            },
        )

        return [to_message(item) for item in payload.get("value", [])]

    def get_message(self, message_id: str) -> EmailMessage:
        payload = self._call(
            f"{self._base()}/messages/{message_id}",
            {"$expand": "attachments($select=id,name,contentType,size)"},
        )

        return to_message(payload)

    def get_thread(self, thread_id: str) -> list[EmailMessage]:
        """Every message in a conversation, oldest first.

        Graph has no "get conversation" call; a conversation is a filter over
        messages. Ordered ascending because a thread reads forwards, and a
        summary of it built backwards would describe the outcome first.
        """

        payload = self._call(
            f"{self._base()}/messages",
            {
                "$filter": f"conversationId eq '{thread_id}'",
                "$select": _LIST_FIELDS,
                "$orderby": "receivedDateTime asc",
                "$top": 50,
            },
        )

        return [to_message(item) for item in payload.get("value", [])]

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """The bytes of one attachment.

        Only `#microsoft.graph.fileAttachment` carries bytes inline. An item
        attachment — a forwarded message or a calendar item — is a resource,
        not a file, and is refused rather than returned as something it is not.
        """

        import base64

        payload = self._call(
            f"{self._base()}/messages/{message_id}/attachments/{attachment_id}"
        )

        content = payload.get("contentBytes")

        if content is None:
            raise EmailSendError(
                "That attachment is not a file attachment, so it has no bytes "
                "to download."
            )

        return base64.b64decode(content)

    # --- writing ---------------------------------------------------------

    def _payload(self, email: OutgoingEmail) -> dict[str, Any]:
        import base64

        message: dict[str, Any] = {
            "subject": email.subject,
            # Text, not HTML. The composer produces plain text, and declaring
            # it as HTML would let a stray angle bracket eat a sentence.
            "body": {"contentType": "Text", "content": email.body},
            "toRecipients": _recipients(email.to_recipients),
        }

        if email.cc_recipients:
            message["ccRecipients"] = _recipients(email.cc_recipients)

        if email.bcc_recipients:
            message["bccRecipients"] = _recipients(email.bcc_recipients)

        if email.attachments:
            message["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": attachment.filename,
                    "contentType": attachment.content_type,
                    "contentBytes": base64.b64encode(attachment.content).decode(
                        "ascii"
                    ),
                }
                for attachment in email.attachments
            ]

        return message

    def create_draft(self, email: OutgoingEmail) -> str:
        payload = self._client.post(f"{self._base()}/messages", self._payload(email))

        if not payload or not payload.get("id"):
            raise EmailSendError("Graph accepted the draft but returned no id.")

        return str(payload["id"])

    def send(self, email: OutgoingEmail) -> SendReceipt:
        """Send through Graph, or raise.

        Two paths, because a reply has to join a conversation rather than start
        one. `/reply` threads correctly and preserves the quoted history;
        `/sendMail` does neither, so using it for a reply would produce a
        message the recipient's client shows detached from the exchange.

        Neither Graph call returns the sent message, so `message_id` is left
        None. That is deliberate: this system does not know the id, and filling
        it with the draft's own id would make a local identifier look like a
        provider's.
        """

        if not email.to_recipients:
            raise EmailSendError("An email needs at least one recipient.")

        message = self._payload(email)

        try:
            if email.in_reply_to_message_id:
                self._client.post(
                    f"{self._base()}/messages/{email.in_reply_to_message_id}/reply",
                    {"message": message},
                )
            else:
                self._client.post(
                    f"{self._base()}/sendMail",
                    {"message": message, "saveToSentItems": True},
                )
        except GraphAuthError as exc:
            raise EmailProviderAuthError(
                f"Microsoft Graph refused the credentials: {exc}"
            ) from exc
        except GraphError as exc:
            raise EmailSendError(
                f"Microsoft Graph did not send the message: {exc}"
            ) from exc

        logger.info(
            "email_sent_via_provider",
            extra={
                "provider": PROVIDER_NAME,
                "recipients": len(email.to_recipients),
                "is_reply": bool(email.in_reply_to_message_id),
                "attachments": len(email.attachments),
            },
        )

        return SendReceipt(provider=PROVIDER_NAME, sent_at=datetime.now(UTC))

    # --- shared ----------------------------------------------------------

    def _call(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """One read, with Graph's failures translated into email ones.

        Every read goes through here so that no caller above this module ever
        has to catch a `GraphError` — which would be the sync module's
        vocabulary leaking into the Email Agent.
        """

        try:
            return self._client.get(path, params)
        except GraphAuthError as exc:
            raise EmailProviderAuthError(
                f"Microsoft Graph refused the credentials: {exc} Mail access is "
                "a separate consent from files."
            ) from exc
        except GraphError as exc:
            raise EmailSendError(f"Microsoft Graph could not be read: {exc}") from exc
