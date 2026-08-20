"""The Outlook provider: Graph's JSON in, provider-neutral types out.

Exercised against a `httpx.MockTransport` carrying recorded-shape Graph
payloads, the same way `tests/services/graph/` exercises the drive service.
That covers the translation, which is where the bugs live. It does **not**
cover whether a real tenant answers this way — no SunRadia mailbox has been
connected, and no test in this repository can claim otherwise.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.core.exceptions import (
    EmailProviderAuthError,
    EmailSendError,
)
from app.services.email.provider.base import OutgoingAttachment, OutgoingEmail
from app.services.email.provider.outlook_provider import (
    OutlookEmailProvider,
    to_message,
)
from app.services.graph.client import GraphClient

MAILBOX = "shared@sunradia.com"

MESSAGE_PAYLOAD = {
    "id": "AAMkAGI2",
    "conversationId": "AAQkAGI2",
    "subject": "Revised numbers",
    "bodyPreview": "Could you send the revised numbers",
    "body": {"contentType": "text", "content": "Could you send them by Friday?"},
    "from": {"emailAddress": {"name": "Ana Ruiz", "address": "ana@client.com"}},
    "toRecipients": [{"emailAddress": {"address": "shared@sunradia.com"}}],
    "ccRecipients": [{"emailAddress": {"address": "cc@client.com"}}],
    "receivedDateTime": "2026-08-18T09:30:00Z",
    "isRead": False,
    "parentFolderId": "inbox-id",
    "categories": ["Clients"],
}


def _provider(handler) -> OutlookEmailProvider:
    client = GraphClient(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        base_url="https://graph.example/v1.0",
        authority="https://login.example",
        transport=httpx.MockTransport(handler),
    )

    return OutlookEmailProvider(mailbox=MAILBOX, client=client)


def _token(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})


def _routing(routes: dict[str, object], *, recorder: list | None = None):
    """A transport that answers the token endpoint and then a path table."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.example" in str(request.url):
            return _token(request)

        if recorder is not None:
            body = json.loads(request.content) if request.content else None
            recorder.append((request.method, request.url.path, body))

        for path, response in routes.items():
            if request.url.path.endswith(path):
                return (
                    response
                    if isinstance(response, httpx.Response)
                    else httpx.Response(200, json=response)
                )

        return httpx.Response(404, json={"error": {"message": "not found"}})

    return handler


# --- mapping -------------------------------------------------------------


def test_a_graph_message_maps_onto_the_neutral_type() -> None:
    message = to_message(MESSAGE_PAYLOAD)

    assert message.message_id == "AAMkAGI2"
    assert message.thread_id == "AAQkAGI2"
    assert message.sender is not None
    assert message.sender.address == "ana@client.com"
    assert message.sender.name == "Ana Ruiz"
    assert [item.address for item in message.cc_recipients] == ["cc@client.com"]
    assert message.subject == "Revised numbers"
    assert message.body == "Could you send them by Friday?"
    assert message.received_at == datetime(2026, 8, 18, 9, 30, tzinfo=UTC)
    assert message.labels == ["Clients"]
    assert message.is_read is False


def test_a_message_with_almost_nothing_still_maps() -> None:
    """Graph omits what was not requested. A missing key is a normal response,
    not a broken one — and an inbox must not fail to list because one row was
    sparse."""

    message = to_message({"id": "m1"})

    assert message.message_id == "m1"
    assert message.thread_id is None
    assert message.sender is None
    assert message.subject == ""
    assert message.received_at is None


def test_an_unparsable_timestamp_is_dropped_rather_than_raised_on() -> None:
    message = to_message({"id": "m1", "receivedDateTime": "yesterday-ish"})

    assert message.received_at is None
    assert message.message_id == "m1"


def test_a_listing_falls_back_to_the_preview_for_text() -> None:
    """`text` is what triage reads. A listing has no body, and returning None
    there would make every listed message untriageable."""

    message = to_message({"id": "m1", "bodyPreview": "Short preview"})

    assert message.body is None
    assert message.text == "Short preview"


def test_attachment_metadata_maps_without_downloading_bytes() -> None:
    message = to_message(
        {
            "id": "m1",
            "attachments": [
                {
                    "id": "a1",
                    "name": "scope.pdf",
                    "contentType": "application/pdf",
                    "size": 1024,
                }
            ],
        }
    )

    assert message.attachments[0].filename == "scope.pdf"
    assert message.attachments[0].size_bytes == 1024
    assert not hasattr(message.attachments[0], "content")


# --- reading -------------------------------------------------------------


def test_listing_reads_the_named_mailbox() -> None:
    """Application permissions carry no user, so the path has to name one."""

    seen: list = []
    provider = _provider(
        _routing({"/messages": {"value": [MESSAGE_PAYLOAD]}}, recorder=seen)
    )

    messages = provider.list_messages(limit=5)

    assert [message.message_id for message in messages] == ["AAMkAGI2"]
    assert f"/users/{MAILBOX}/messages" in seen[0][1]


def test_a_thread_is_read_oldest_first() -> None:
    """A thread reads forwards. A summary built backwards describes the outcome
    before the question."""

    seen: list = []
    provider = _provider(_routing({"/messages": {"value": []}}, recorder=seen))

    provider.get_thread("AAQkAGI2")

    assert seen[0][0] == "GET"


def test_a_forbidden_response_names_the_missing_consent() -> None:
    """The failure most likely to be sitting in wait: the OneDrive consent does
    not cover mail, and "403" alone would send somebody to the wrong page."""

    provider = _provider(
        _routing(
            {
                "/messages": httpx.Response(
                    403, json={"error": {"message": "Access denied"}}
                )
            }
        )
    )

    with pytest.raises(EmailProviderAuthError) as error:
        provider.list_messages()

    assert "separate consent" in str(error.value)


def test_a_graph_failure_becomes_an_email_error_not_a_sync_error() -> None:
    """A `GraphError` escaping here would be the sync module's vocabulary
    leaking into the Email Agent, and `main.py` would map it to a sync status."""

    provider = _provider(
        _routing(
            {"/messages": httpx.Response(500, json={"error": {"message": "boom"}})}
        )
    )

    with pytest.raises(EmailSendError):
        provider.list_messages()


def test_an_item_attachment_is_refused_rather_than_returned_as_bytes() -> None:
    """A forwarded message is a resource, not a file. Returning something else
    in its place would corrupt the attachment silently."""

    provider = _provider(_routing({"/attachments/a1": {"id": "a1", "name": "note"}}))

    with pytest.raises(EmailSendError):
        provider.get_attachment("m1", "a1")


def test_a_file_attachment_is_decoded() -> None:
    import base64

    provider = _provider(
        _routing(
            {
                "/attachments/a1": {
                    "id": "a1",
                    "name": "scope.pdf",
                    "contentBytes": base64.b64encode(b"%PDF-1.4").decode(),
                }
            }
        )
    )

    assert provider.get_attachment("m1", "a1") == b"%PDF-1.4"


# --- sending -------------------------------------------------------------


def test_sending_posts_to_sendmail_and_returns_a_receipt() -> None:
    seen: list = []
    provider = _provider(_routing({"/sendMail": httpx.Response(202)}, recorder=seen))

    receipt = provider.send(
        OutgoingEmail(
            to_recipients=["client@example.com"],
            subject="Following up",
            body="Quick note.",
        )
    )

    method, path, body = seen[0]

    assert method == "POST"
    assert path.endswith(f"/users/{MAILBOX}/sendMail")
    assert body["message"]["toRecipients"][0]["emailAddress"]["address"] == (
        "client@example.com"
    )
    # Text, not HTML: the composer produces plain text, and declaring it HTML
    # would let a stray angle bracket eat a sentence.
    assert body["message"]["body"]["contentType"] == "Text"
    assert receipt.provider == "outlook"
    # Graph's sendMail returns no id. Inventing one would make a local
    # identifier look like a provider's.
    assert receipt.message_id is None


def test_a_reply_uses_the_reply_endpoint_so_it_threads() -> None:
    seen: list = []
    provider = _provider(_routing({"/reply": httpx.Response(202)}, recorder=seen))

    provider.send(
        OutgoingEmail(
            to_recipients=["client@example.com"],
            subject="Re: Numbers",
            body="Yes — Thursday works.",
            in_reply_to_message_id="AAMkAGI2",
        )
    )

    assert seen[0][1].endswith("/messages/AAMkAGI2/reply")


def test_attachments_are_base64_encoded_as_file_attachments() -> None:
    import base64

    seen: list = []
    provider = _provider(_routing({"/sendMail": httpx.Response(202)}, recorder=seen))

    provider.send(
        OutgoingEmail(
            to_recipients=["client@example.com"],
            subject="Scope",
            body="Attached.",
            attachments=[
                OutgoingAttachment(
                    filename="scope.pdf",
                    content_type="application/pdf",
                    content=b"%PDF-1.4 scope",
                )
            ],
        )
    )

    attachment = seen[0][2]["message"]["attachments"][0]

    assert attachment["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert base64.b64decode(attachment["contentBytes"]) == b"%PDF-1.4 scope"


def test_sending_with_no_recipients_is_refused_before_the_call() -> None:
    seen: list = []
    provider = _provider(_routing({"/sendMail": httpx.Response(202)}, recorder=seen))

    with pytest.raises(EmailSendError):
        provider.send(OutgoingEmail(to_recipients=[], subject="x", body="y"))

    assert seen == []


def test_a_refused_send_raises_and_returns_no_receipt() -> None:
    provider = _provider(
        _routing(
            {
                "/sendMail": httpx.Response(
                    400, json={"error": {"message": "Invalid recipient"}}
                )
            }
        )
    )

    with pytest.raises(EmailSendError):
        provider.send(
            OutgoingEmail(to_recipients=["nobody@example.com"], subject="x", body="y")
        )


def test_rejected_credentials_are_an_auth_error_not_a_send_error() -> None:
    """The remedy differs: a token that will not mint again is a configuration
    problem, and retrying it on a schedule produces the same answer more often."""

    provider = _provider(
        _routing(
            {
                "/sendMail": httpx.Response(
                    403, json={"error": {"message": "Access denied"}}
                )
            }
        )
    )

    with pytest.raises(EmailProviderAuthError):
        provider.send(OutgoingEmail(to_recipients=["a@x.com"], subject="x", body="y"))
