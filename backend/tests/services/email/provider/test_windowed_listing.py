"""A windowed listing — the report path — reads bodies and leaves drafts out.

Driven through the real `GraphClient` with an `httpx.MockTransport` standing in
for Graph, so the request the provider builds is the request that ships.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.email.provider.outlook_provider import (
    WINDOW_PAGE_SIZE,
    OutlookEmailProvider,
)
from app.services.features.reports import correspondence
from app.services.features.reports.period import range_between
from app.services.graph.client import GraphClient

MAILBOX = "owner@example.com"

_BODY = (
    "<html><head><style>p { color: red; }</style></head><body>"
    "<p>Ravi agreed to send the proposal by Friday.</p>"
    + "<p>Filler.</p>" * 60
    + "<p>The client also requires a signed NDA first.</p></body></html>"
)


def _message(
    message_id: str, *, draft: bool = False, sender: str | None = "ravi@client.com"
) -> dict:
    return {
        "id": message_id,
        "conversationId": f"conv-{message_id}",
        "subject": "Proposal",
        "bodyPreview": "Ravi agreed to send the proposal by Friday.",
        "body": {"contentType": "html", "content": _BODY},
        "from": {"emailAddress": {"address": sender}} if sender else None,
        "toRecipients": [{"emailAddress": {"address": MAILBOX}}],
        "receivedDateTime": "2026-06-10T09:00:00Z",
        "isDraft": draft,
    }


def _provider(seen: list[dict[str, str]]) -> OutlookEmailProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        query = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        seen.append(query)
        fields = set(query.get("$select", "").split(","))
        rows = [_message("m-1"), _message("d-1", draft=True, sender=None)]

        if "sentitems" in request.url.path:
            rows = []

        return httpx.Response(
            200,
            json={
                "value": [
                    {k: v for k, v in r.items() if k in fields or k == "id"}
                    for r in rows
                ]
            },
        )

    client = GraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        transport=httpx.MockTransport(handler),
    )

    return OutlookEmailProvider(mailbox=MAILBOX, client=client)


def test_windowed_listing_asks_for_bodies_and_drops_drafts() -> None:
    seen: list[dict[str, str]] = []
    messages = _provider(seen).list_messages(
        limit=100,
        since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 7, 1, tzinfo=UTC),
    )

    assert [m.message_id for m in messages] == ["m-1"]
    assert "body" in seen[0]["$select"].split(",")
    assert "isDraft" in seen[0]["$select"].split(",")
    assert seen[0]["$top"] == str(WINDOW_PAGE_SIZE)
    assert seen[0]["$filter"] == (
        "receivedDateTime ge 2026-06-01T00:00:00Z and "
        "receivedDateTime lt 2026-07-01T00:00:00Z"
    )


def test_unwindowed_listing_is_unchanged() -> None:
    seen: list[dict[str, str]] = []
    messages = _provider(seen).list_messages(limit=25)

    assert "body" not in seen[0]["$select"].split(",")
    assert seen[0]["$top"] == "25"
    # The inbox view's behaviour is not this change's business.
    assert len(messages) == 2


def test_report_correspondence_reads_the_whole_body_as_text() -> None:
    seen: list[dict[str, str]] = []
    result = correspondence.from_provider(
        _provider(seen),
        mailbox=MAILBOX,
        period=range_between(
            datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)
        ),
    )

    assert [m.message_id for m in result.messages] == ["m-1"]
    body = result.messages[0].body
    assert "signed NDA" in body
    assert "<p>" not in body
    assert "color: red" not in body


def test_plain_text_with_angle_brackets_is_left_alone() -> None:
    text = "From Ravi <ravi@client.com>: if a < b then send."

    assert correspondence._html_to_text(text) == text
