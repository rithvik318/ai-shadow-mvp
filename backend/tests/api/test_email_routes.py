"""The Email Agent's HTTP surface.

These assert behaviour, not status codes for their own sake: that a body cannot
name a different owner, that an unconnected mailbox is a stated state rather
than invented mail, and that the approve-then-send path cannot be short-cut.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import EmailSendError
from app.models.email import EmailCategory, EmailPriority
from app.models.user import User
from app.services.features.email import mailbox_service, sending_service
from app.services.features.email.composer_service import GeneratedEmail
from app.services.features.email.triage_service import ThreadSummary, TriageVerdict
from tests.support.email import RecordingProvider, message

GENERATED = GeneratedEmail(subject="Following up", body="Quick note about the pilot.")

VERDICT = TriageVerdict(
    category=EmailCategory.NEEDS_REPLY,
    priority=EmailPriority.HIGH,
    summary="They want the revised numbers.",
    suggested_action="Send them today.",
    action_items=["Send revised numbers"],
    follow_up_recommended=True,
    follow_up_reason="They are waiting.",
)


@pytest.fixture(autouse=True)
def no_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with no mailbox connected.

    Without this the suite would pass or fail depending on whose `.env` it ran
    against, and "no mailbox" is the state most of these tests are about.
    """

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_PROVIDER", None)
    monkeypatch.setattr(settings_module.settings, "EMAIL_MAILBOX_ADDRESS", None)


def _connect(monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider) -> None:
    """Point both service-level provider lookups at a test provider.

    Patched at the two modules that import `get_provider` by name, rather than
    at the registry, so the test cannot accidentally pass because the registry
    grew a fallback.
    """

    monkeypatch.setattr(sending_service, "get_provider", lambda: provider)
    monkeypatch.setattr(mailbox_service, "get_provider", lambda: provider)


# --- identity ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/email/templates"),
        ("get", "/email/drafts"),
        ("post", "/email/compose"),
        ("get", "/email/messages"),
        ("get", "/email/follow-ups"),
        ("get", "/email/assessments"),
    ],
)
def test_every_scoped_endpoint_requires_an_identity(
    client: TestClient, method: str, path: str
) -> None:
    """Every endpoint that touches a person's own data refuses an anonymous
    caller, whatever the verb.

    Sent through `client.request` rather than `client.get(json=...)`: httpx's
    per-verb helpers for bodyless methods take no `json` argument, and only the
    one POST here needs a body at all. Passing a body on GET was a mistake in
    the test, not a gap in the route — the routes were always correct.
    """

    response = client.request(
        method.upper(), path, json={} if method == "post" else None
    )

    assert response.status_code == 401
    assert response.json()["error"] == "MissingIdentityError"


def test_the_provider_status_needs_no_identity(client: TestClient) -> None:
    """A setup notice must render before anybody has chosen a twin."""

    response = client.get("/email/provider/status")

    assert response.status_code == 200
    assert response.json()["configured"] is False


# --- templates -----------------------------------------------------------


def test_a_template_round_trips(client: TestClient, user_headers: dict) -> None:
    created = client.post(
        "/email/templates",
        headers=user_headers,
        json={
            "name": "Intro",
            "subject_template": "Hello from {{ company }}",
            "body_template": "Hi {{ name }}, I'm {{ sender }} at {{ company }}.",
            "category": "introduction",
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["placeholders"] == ["company", "name", "sender"]

    listed = client.get("/email/templates", headers=user_headers).json()

    assert listed["total"] == 1


def test_a_template_is_invisible_to_another_user(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    created = client.post(
        "/email/templates",
        headers=user_headers,
        json={
            "name": "Intro",
            "subject_template": "Hello",
            "body_template": "Hi there.",
        },
    ).json()

    assert client.get("/email/templates", headers=user_b_headers).json()["total"] == 0
    assert (
        client.get(
            f"/email/templates/{created['id']}", headers=user_b_headers
        ).status_code
        == 404
    )


def test_a_duplicate_template_name_is_a_conflict(
    client: TestClient, user_headers: dict
) -> None:
    payload = {
        "name": "Intro",
        "subject_template": "Hello",
        "body_template": "Hi there.",
    }
    client.post("/email/templates", headers=user_headers, json=payload)

    assert (
        client.post("/email/templates", headers=user_headers, json=payload).status_code
        == 409
    )


def test_filling_a_template_needs_no_model_call(
    client: TestClient, user_headers: dict
) -> None:
    """Paying for a completion to replace `{{ name }}` with a name would be
    slower, dearer and less predictable than substitution."""

    created = client.post(
        "/email/templates",
        headers=user_headers,
        json={
            "name": "Intro",
            "subject_template": "Hello from {{ company }}",
            "body_template": "Hi {{ name }}, about {{ topic }}.",
        },
    ).json()

    filled = client.post(
        f"/email/templates/{created['id']}/fill",
        headers=user_headers,
        json={"values": {"company": "SunRadia", "name": "Ana"}},
    ).json()

    assert filled["subject"] == "Hello from SunRadia"
    assert filled["body"] == "Hi Ana, about {{ topic }}."
    assert filled["missing"] == ["topic"]


# --- drafts and the approval path ----------------------------------------


def _create_draft(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload: dict = {
        "to_recipients": ["client@example.com"],
        "subject": "Following up",
        "body": "Just checking in.",
    }
    payload.update(overrides)

    return client.post("/email/drafts", headers=headers, json=payload).json()


def test_a_draft_body_cannot_name_a_different_owner(
    client: TestClient, user_headers: dict, test_user: User, test_user_b: User
) -> None:
    """`X-User-ID` is the only thing that decides ownership. A stray `user_id`
    in a body is ignored, not honoured."""

    draft = _create_draft(client, user_headers, user_id=str(test_user_b.id))

    assert (
        client.get(f"/email/drafts/{draft['id']}", headers=user_headers).status_code
        == 200
    )


def test_a_malformed_recipient_is_rejected(
    client: TestClient, user_headers: dict
) -> None:
    response = client.post(
        "/email/drafts",
        headers=user_headers,
        json={"to_recipients": ["not-an-address"], "subject": "x", "body": "y"},
    )

    assert response.status_code == 422


def test_sending_an_unapproved_draft_is_refused(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate, over HTTP. A connected mailbox does not change the answer."""

    provider = RecordingProvider()
    _connect(monkeypatch, provider)
    draft = _create_draft(client, user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/send",
        headers=user_headers,
        json={"confirm": True},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "EmailDraftNotApprovedError"
    assert provider.sent == []


def test_sending_without_confirmation_is_rejected(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two cheap gates on an irreversible action is the right number."""

    provider = RecordingProvider()
    _connect(monkeypatch, provider)
    draft = _create_draft(client, user_headers)
    client.post(f"/email/drafts/{draft['id']}/approve", headers=user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/send",
        headers=user_headers,
        json={"confirm": False},
    )

    assert response.status_code == 422
    assert provider.sent == []


def test_the_full_approve_then_send_path(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider()
    _connect(monkeypatch, provider)
    draft = _create_draft(client, user_headers)

    approved = client.post(
        f"/email/drafts/{draft['id']}/approve", headers=user_headers
    ).json()

    assert approved["status"] == "approved"

    sent = client.post(
        f"/email/drafts/{draft['id']}/send",
        headers=user_headers,
        json={"confirm": True},
    ).json()

    assert sent["status"] == "sent"
    assert sent["sent_at"] is not None
    assert len(provider.sent) == 1


def test_editing_after_approval_forces_a_second_approval(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider()
    _connect(monkeypatch, provider)
    draft = _create_draft(client, user_headers)
    client.post(f"/email/drafts/{draft['id']}/approve", headers=user_headers)

    edited = client.patch(
        f"/email/drafts/{draft['id']}",
        headers=user_headers,
        json={"body": "Please wire the deposit."},
    ).json()

    assert edited["status"] == "draft"

    response = client.post(
        f"/email/drafts/{draft['id']}/send",
        headers=user_headers,
        json={"confirm": True},
    )

    assert response.status_code == 409
    assert provider.sent == []


def test_with_no_mailbox_sending_is_a_conflict_and_nothing_is_marked_sent(
    client: TestClient, user_headers: dict
) -> None:
    """The property the whole module is built around: no simulated provider
    exists, so there is no path to a false `sent`."""

    draft = _create_draft(client, user_headers)
    client.post(f"/email/drafts/{draft['id']}/approve", headers=user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/send",
        headers=user_headers,
        json={"confirm": True},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "EmailProviderNotConfiguredError"

    after = client.get(f"/email/drafts/{draft['id']}", headers=user_headers).json()

    assert after["status"] == "approved"
    assert after["sent_at"] is None


def test_a_provider_failure_is_a_bad_gateway_and_a_failed_draft(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect(monkeypatch, RecordingProvider(fail_with=EmailSendError("Refused.")))
    draft = _create_draft(client, user_headers)
    client.post(f"/email/drafts/{draft['id']}/approve", headers=user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/send",
        headers=user_headers,
        json={"confirm": True},
    )

    assert response.status_code == 502

    after = client.get(f"/email/drafts/{draft['id']}", headers=user_headers).json()

    assert after["status"] == "failed"
    assert after["sent_at"] is None
    assert after["send_error"] == "Refused."


def test_another_users_draft_is_not_found(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    draft = _create_draft(client, user_headers)

    assert (
        client.get(f"/email/drafts/{draft['id']}", headers=user_b_headers).status_code
        == 404
    )
    assert client.get("/email/drafts", headers=user_b_headers).json()["total"] == 0


# --- attachments ---------------------------------------------------------


def test_an_attachment_is_described_but_its_bytes_are_never_returned(
    client: TestClient, user_headers: dict
) -> None:
    draft = _create_draft(client, user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/attachments",
        headers=user_headers,
        files={"file": ("scope.pdf", b"%PDF-1.4 scope", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "scope.pdf"
    assert body["size_bytes"] == len(b"%PDF-1.4 scope")
    assert "content" not in body

    with_attachment = client.get(
        f"/email/drafts/{draft['id']}", headers=user_headers
    ).json()

    assert len(with_attachment["attachments"]) == 1
    assert "content" not in with_attachment["attachments"][0]


def test_an_empty_attachment_is_unprocessable(
    client: TestClient, user_headers: dict
) -> None:
    draft = _create_draft(client, user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/attachments",
        headers=user_headers,
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 422


def test_an_oversized_attachment_is_too_large(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "EMAIL_MAX_ATTACHMENT_BYTES", 4)
    draft = _create_draft(client, user_headers)

    response = client.post(
        f"/email/drafts/{draft['id']}/attachments",
        headers=user_headers,
        files={"file": ("big.pdf", b"far too many bytes", "application/pdf")},
    )

    assert response.status_code == 413


# --- compose -------------------------------------------------------------


def test_compose_returns_text_and_saves_nothing(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    response = client.post(
        "/email/compose",
        headers=user_headers,
        json={
            "operation": "generate",
            "instruction": "Write a concise follow-up after yesterday's meeting.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Following up"
    assert body["knowledge_used"] is False
    assert client.get("/email/drafts", headers=user_headers).json()["total"] == 0


def test_compose_reports_that_no_company_knowledge_was_used(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    """A caller can tell the difference between "grounded" and "written from
    nothing", which is the whole reason the flag exists."""

    calls = fake_analysis(GENERATED)

    body = client.post(
        "/email/compose",
        headers=user_headers,
        json={
            "operation": "generate",
            "instruction": "Describe our AI and ML services.",
            "use_knowledge_base": True,
        },
    ).json()

    assert body["knowledge_used"] is False
    assert body["sources"] == []
    assert (
        "Do not state any fact about the sender's company" in calls[0][2]["knowledge"]
    )


def test_compose_without_an_instruction_is_unprocessable(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    response = client.post(
        "/email/compose", headers=user_headers, json={"operation": "generate"}
    )

    assert response.status_code == 422


def test_a_rewrite_needs_a_draft_to_rewrite(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(GENERATED)

    response = client.post(
        "/email/compose", headers=user_headers, json={"operation": "shorten"}
    )

    assert response.status_code == 422


def test_a_generated_draft_is_saved_as_needing_review(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    """`needs_review` is not `approved`. A person still has to read it."""

    fake_analysis(GENERATED)

    generated = client.post(
        "/email/compose",
        headers=user_headers,
        json={"operation": "generate", "instruction": "Follow up on the pilot."},
    ).json()

    draft = client.post(
        "/email/drafts",
        headers=user_headers,
        json={
            "to_recipients": ["client@example.com"],
            "subject": generated["subject"],
            "body": generated["body"],
            "generated_by_ai": True,
        },
    ).json()

    assert draft["status"] == "needs_review"
    assert draft["generated_by_ai"] is True


# --- mailbox and triage --------------------------------------------------


def test_the_inbox_says_not_connected_rather_than_returning_nothing(
    client: TestClient, user_headers: dict
) -> None:
    """ "No mail" and "no mailbox" are different facts, and a person looking at
    an empty list should not have to guess which one they are seeing."""

    response = client.get("/email/messages", headers=user_headers)

    assert response.status_code == 409
    assert response.json()["error"] == "EmailProviderNotConfiguredError"


def test_a_connected_inbox_lists_real_messages_with_no_assessment_yet(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect(monkeypatch, RecordingProvider(messages=[message()]))

    body = client.get("/email/messages", headers=user_headers).json()

    assert body["total"] == 1
    assert body["items"][0]["message"]["subject"] == "Proposal follow-up"
    # Null means "not yet judged", never "judged unimportant".
    assert body["items"][0]["assessment"] is None


def test_a_supplied_message_can_be_triaged_with_no_mailbox(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    """What makes triage usable before Outlook is connected — and what proves
    the classification path is real rather than waiting on credentials."""

    fake_analysis(VERDICT)

    response = client.post(
        "/email/triage",
        headers=user_headers,
        json={
            "message": {
                "message_id": "pasted-1",
                "sender": "client@example.com",
                "subject": "Revised numbers",
                "body": "Could you send them by Friday?",
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "needs_reply"
    assert body["priority"] == "high"
    assert body["action_items"] == ["Send revised numbers"]


def test_triage_needs_exactly_one_of_id_or_message(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    """Both would leave the answer attached to a message id it may not
    describe."""

    fake_analysis(VERDICT)

    neither = client.post("/email/triage", headers=user_headers, json={})
    both = client.post(
        "/email/triage",
        headers=user_headers,
        json={
            "message_id": "m1",
            "message": {"message_id": "m1", "subject": "x", "body": "y"},
        },
    )

    assert neither.status_code == 422
    assert both.status_code == 422


def test_an_unpersisted_triage_does_not_reach_the_lists(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(VERDICT)

    response = client.post(
        "/email/triage",
        headers=user_headers,
        json={
            "persist": False,
            "message": {
                "message_id": "pasted-1",
                "subject": "Revised numbers",
                "body": "Could you send them by Friday?",
            },
        },
    )

    # The result is a complete assessment even though no row was written. The
    # column defaults for `id` and `handled` only fire on INSERT, so the
    # service sets them explicitly — otherwise the response model rejects a
    # perfectly good answer.
    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["handled"] is False
    assert body["category"] == "needs_reply"

    assert client.get("/email/assessments", headers=user_headers).json()["total"] == 0
    assert client.get("/email/follow-ups", headers=user_headers).json()["total"] == 0


def test_a_triaged_message_appears_in_follow_ups_and_can_be_handled(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(VERDICT)

    assessment = client.post(
        "/email/triage",
        headers=user_headers,
        json={
            "message": {
                "message_id": "m1",
                "subject": "Revised numbers",
                "body": "Could you send them by Friday?",
            }
        },
    ).json()

    assert client.get("/email/follow-ups", headers=user_headers).json()["total"] == 1

    handled = client.post(
        f"/email/follow-ups/{assessment['id']}/handled",
        headers=user_headers,
        json={"handled": True},
    ).json()

    assert handled["handled"] is True
    assert client.get("/email/follow-ups", headers=user_headers).json()["total"] == 0


def test_follow_ups_are_scoped_to_the_current_user(
    client: TestClient, user_headers: dict, user_b_headers: dict, fake_analysis
) -> None:
    fake_analysis(VERDICT)

    client.post(
        "/email/triage",
        headers=user_headers,
        json={"message": {"message_id": "m1", "subject": "x", "body": "Please reply."}},
    )

    assert client.get("/email/follow-ups", headers=user_b_headers).json()["total"] == 0


def test_a_thread_can_be_summarised_from_supplied_messages(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(
        ThreadSummary(
            summary="Pricing agreed; scope open.",
            action_items=["Confirm the delivery date"],
        )
    )

    body = client.post(
        "/email/threads/summarize",
        headers=user_headers,
        json={
            "messages": [
                {"message_id": "m1", "subject": "Scope", "body": "What is included?"},
                {
                    "message_id": "m2",
                    "subject": "Re: Scope",
                    "body": "Everything but hosting.",
                },
            ]
        },
    ).json()

    assert body["summary"].startswith("Pricing agreed")
    assert body["message_count"] == 2


def test_summarising_needs_exactly_one_of_thread_or_messages(
    client: TestClient, user_headers: dict, fake_analysis
) -> None:
    fake_analysis(ThreadSummary(summary=""))

    response = client.post("/email/threads/summarize", headers=user_headers, json={})

    assert response.status_code == 422


def test_assessments_are_readable_while_the_mailbox_is_unreachable(
    client: TestClient, user_headers: dict, fake_analysis, db_session: Session
) -> None:
    """Reads the database only, so a provider outage does not blank the triage
    view."""

    fake_analysis(VERDICT)
    client.post(
        "/email/triage",
        headers=user_headers,
        json={"message": {"message_id": "m1", "subject": "x", "body": "Please reply."}},
    )

    body = client.get("/email/assessments", headers=user_headers).json()

    assert body["total"] == 1
    assert body["items"][0]["provider_message_id"] == "m1"
