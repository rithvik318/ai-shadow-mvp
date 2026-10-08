"""`from_provider`: the live half of the evidence layer.

Everything else in this package is exercised against the collected JSON, which
is the right default — it is real correspondence and needs no tenant. The
consequence was that the branch a deployed report actually runs had no test at
all, and it shipped reading a method (`list_sent_messages`) that the
`EmailProvider` protocol does not declare and no provider implements. Every
live report therefore described the inbox and none of the replies.

These tests run against `RecordingProvider`, which satisfies the same protocol
the Outlook provider does, so a future provider that gets folders wrong fails
here rather than in somebody's browser.
"""

from datetime import UTC, datetime, timedelta

from app.services.features.reports.correspondence import (
    SENT_FOLDER,
    Direction,
    from_provider,
)
from app.services.features.reports.period import range_between
from tests.support.email import RecordingProvider
from tests.support.email import message as provider_message

MAILBOX = "owner@sunradia.com"
START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 14, tzinfo=UTC)
PERIOD = range_between(START, END)


def inbound(**overrides):
    defaults = {
        "message_id": "in-1",
        "thread_id": "thread-1",
        "sender": "client@acme.example",
        "subject": "Revised delivery schedule",
        "received_at": START + timedelta(days=2),
    }

    return provider_message(**{**defaults, **overrides})


def outbound(**overrides):
    defaults = {
        "message_id": "out-1",
        "thread_id": "thread-1",
        "sender": MAILBOX,
        "subject": "RE: Revised delivery schedule",
        "received_at": START + timedelta(days=2, hours=3),
        "folder": SENT_FOLDER,
    }

    return provider_message(**{**defaults, **overrides})


class TestBothDirections:
    def test_the_sent_folder_is_read(self) -> None:
        provider = RecordingProvider(messages=[inbound(), outbound()])

        correspondence = from_provider(provider, mailbox=MAILBOX, period=PERIOD)

        assert [message.message_id for message in correspondence.inbound] == ["in-1"]
        assert [message.message_id for message in correspondence.outbound] == ["out-1"]

    def test_a_sent_message_from_another_address_is_still_outbound(self) -> None:
        """Send-on-behalf and shared mailboxes.

        The folder is the evidence that a message left. The `From` header is
        not, and reading it instead turns the owner's own replies into mail
        they received.
        """

        provider = RecordingProvider(
            messages=[outbound(sender="assistant@sunradia.com")]
        )

        correspondence = from_provider(provider, mailbox=MAILBOX, period=PERIOD)

        assert len(correspondence.outbound) == 1
        assert correspondence.outbound[0].direction is Direction.OUTBOUND

    def test_a_message_appearing_in_both_listings_is_counted_once(self) -> None:
        message = outbound()
        provider = RecordingProvider(messages=[message, message])

        correspondence = from_provider(provider, mailbox=MAILBOX, period=PERIOD)

        assert len(correspondence.messages) == 1


class TestTheWindow:
    def test_messages_outside_the_period_are_excluded(self) -> None:
        provider = RecordingProvider(
            messages=[
                inbound(message_id="before", received_at=START - timedelta(days=5)),
                inbound(message_id="inside"),
                inbound(message_id="after", received_at=END + timedelta(days=5)),
            ]
        )

        correspondence = from_provider(provider, mailbox=MAILBOX, period=PERIOD)

        assert [m.message_id for m in correspondence.messages] == ["inside"]

    def test_the_period_is_asked_for_rather_than_filtered_afterwards(self) -> None:
        """The window goes to the provider.

        Asking for the newest messages and discarding the rest reads a whole
        mailbox to describe a week of it, and answers a period older than one
        page with nothing at all. Both listings — inbox and sent — must carry
        the same bounds.
        """

        provider = RecordingProvider(messages=[inbound(), outbound()])

        from_provider(provider, mailbox=MAILBOX, period=PERIOD)

        assert [call["folder"] for call in provider.listed] == [None, SENT_FOLDER]

        for call in provider.listed:
            assert call["since"] == PERIOD.start
            assert call["until"] == PERIOD.end

    def test_a_window_the_provider_ignores_is_still_applied(self) -> None:
        """`since`/`until` are a request, not a guarantee.

        A provider that cannot filter server-side may hand back everything, and
        a message outside the period must not reach a report either way.
        """

        class Unfiltered(RecordingProvider):
            def list_messages(self, *, limit=25, folder=None, since=None, until=None):
                return super().list_messages(limit=limit, folder=folder)

        provider = Unfiltered(
            messages=[
                inbound(message_id="before", received_at=START - timedelta(days=30)),
                inbound(message_id="inside"),
            ]
        )

        correspondence = from_provider(provider, mailbox=MAILBOX, period=PERIOD)

        assert [m.message_id for m in correspondence.messages] == ["inside"]

    def test_a_full_page_is_reported_as_truncated(self) -> None:
        """Everything the caller allowed came back, so there is likely more.

        The alternative is a report that describes part of a month and presents
        it as the month, which is the one thing this module must not do.
        """

        provider = RecordingProvider(
            messages=[inbound(message_id=f"in-{index}") for index in range(4)]
        )

        correspondence = from_provider(
            provider, mailbox=MAILBOX, period=PERIOD, limit=2
        )

        assert correspondence.truncated is True
        assert any("maximum this report reads" in n for n in correspondence.limitations)

    def test_a_partial_page_is_the_whole_period(self) -> None:
        provider = RecordingProvider(messages=[inbound(), outbound()])

        correspondence = from_provider(
            provider, mailbox=MAILBOX, period=PERIOD, limit=50
        )

        assert correspondence.truncated is False
        assert correspondence.limitations == []

    def test_an_empty_mailbox_is_empty_rather_than_truncated(self) -> None:
        correspondence = from_provider(
            RecordingProvider(messages=[]), mailbox=MAILBOX, period=PERIOD
        )

        assert correspondence.messages == []
        assert correspondence.truncated is False
