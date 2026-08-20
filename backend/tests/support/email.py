"""Shared fixtures for the Email Agent tests.

Two hooks, at two different depths, on purpose.

`fake_analysis` replaces `analysis_engine.run` and records the prompt name and
every template variable it was given. That is what lets a test assert *what the
model was shown* — that this user's profile is in the prompt and nobody else's,
that the no-knowledge marker is present when retrieval found nothing — rather
than asserting on a reply the test itself chose.

`fake_llm` (from `tests.support.llm`) is left alone and used directly by the
tests that must exercise the real `AnalysisEngine`: prompt rendering, fenced
JSON, and validation against the response model. Faking only the top layer
everywhere would leave the actual prompt templates untested.

`RecordingProvider` is a real `EmailProvider` implementation for tests. It is
**not** a fallback and is not importable from `app` — no shipped code path can
reach it, which is what keeps "there is no provider that fakes a send" true of
the application rather than merely of the current configuration.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest

from app.core.exceptions import EmailSendError
from app.services.email.provider.base import (
    EmailAddress,
    EmailMessage,
    OutgoingEmail,
    ProviderStatus,
    SendReceipt,
)
from app.services.engines.analysis.analysis_engine import analysis_engine


class RecordingProvider:
    """A provider that records what it was asked to do.

    `fail_with` makes `send` raise, which is how the "a failed send is never
    recorded as sent" tests are written — the interesting assertion is about
    the draft's state afterwards, and that needs a provider that genuinely
    refuses.
    """

    def __init__(
        self,
        *,
        name: str = "recording",
        messages: list[EmailMessage] | None = None,
        fail_with: Exception | None = None,
        message_id: str | None = "provider-message-1",
    ) -> None:
        self.name = name
        self.sent: list[OutgoingEmail] = []
        self.drafted: list[OutgoingEmail] = []
        self._messages = messages or []
        self._fail_with = fail_with
        self._message_id = message_id

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            configured=True,
            connected=True,
            mailbox="test@example.com",
        )

    def list_messages(
        self, *, limit: int = 25, folder: str | None = None
    ) -> list[EmailMessage]:
        selected = [
            message
            for message in self._messages
            if folder is None or message.folder == folder
        ]

        return selected[:limit]

    def get_message(self, message_id: str) -> EmailMessage:
        for message in self._messages:
            if message.message_id == message_id:
                return message

        raise EmailSendError(f"No message {message_id}")

    def get_thread(self, thread_id: str) -> list[EmailMessage]:
        return [message for message in self._messages if message.thread_id == thread_id]

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        return b"attachment-bytes"

    def create_draft(self, email: OutgoingEmail) -> str:
        self.drafted.append(email)
        return "provider-draft-1"

    def send(self, email: OutgoingEmail) -> SendReceipt:
        if self._fail_with is not None:
            raise self._fail_with

        self.sent.append(email)

        return SendReceipt(
            provider=self.name,
            sent_at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
            message_id=self._message_id,
        )


def message(
    *,
    message_id: str = "m1",
    thread_id: str | None = "t1",
    sender: str = "client@example.com",
    subject: str = "Proposal follow-up",
    body: str = "Could you send the revised numbers by Friday?",
    folder: str | None = None,
    received_at: datetime | None = None,
) -> EmailMessage:
    """One provider-neutral message, with sensible defaults."""

    return EmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        sender=EmailAddress(address=sender),
        to_recipients=[EmailAddress(address="me@sunradia.com")],
        subject=subject,
        snippet=body[:80],
        body=body,
        received_at=received_at or datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
        folder=folder,
    )


@pytest.fixture
def fake_analysis(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable]:
    """Pin what the engine returns, and record what it was asked.

    Returns an installer taking one result or a list of them. Calling it yields
    the list that accumulates `(prompt_name, response_model, variables)` per
    call, so a test can assert on the exact prompt variables — which is the
    only way to check that a persona, or the absence of company knowledge,
    actually reached the model.
    """

    calls: list[tuple[str, type, dict]] = []

    def install(results: object | list[object]) -> list[tuple[str, type, dict]]:
        queued = list(results) if isinstance(results, list) else [results]

        def run(prompt_name: str, response_model: type, **variables: object) -> object:
            calls.append((prompt_name, response_model, dict(variables)))

            # The last result repeats, so a test that does not care how many
            # calls happen does not have to count them.
            index = min(len(calls) - 1, len(queued) - 1)

            return queued[index]

        monkeypatch.setattr(analysis_engine, "run", run)

        return calls

    yield install
