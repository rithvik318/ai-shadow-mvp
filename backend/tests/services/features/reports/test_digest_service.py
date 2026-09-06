"""What the email digest counts, and what it refuses to claim.

The refusals are the point of these tests. A digest is a set of counts, and a
count is a claim about what was measured — so the interesting failures are all
of the form "reported a number it had no right to".
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import EmailProviderNotConfiguredError
from app.models.email import (
    EmailAssessment,
    EmailCategory,
    EmailDraft,
    EmailDraftStatus,
    EmailPriority,
)
from app.models.user import User
from app.services.email.provider.base import EmailMessage
from app.services.features.reports import digest_service
from app.services.features.reports.period import week_of
from tests.support.email import RecordingProvider, message

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
WEEK = week_of(datetime(2026, 8, 26, tzinfo=UTC))  # 24–31 August.

INSIDE = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
BEFORE = WEEK.start - timedelta(hours=1)


def triaged(
    db: Session,
    *,
    user: User,
    message_id: str,
    category: EmailCategory = EmailCategory.NEEDS_REPLY,
    priority: EmailPriority = EmailPriority.NORMAL,
    follow_up: bool = False,
    provider: str = "recording",
) -> EmailAssessment:
    row = EmailAssessment(
        user_id=user.id,
        provider=provider,
        provider_message_id=message_id,
        subject="Proposal follow-up",
        category=category,
        priority=priority,
        summary="They are waiting on the revised numbers.",
        follow_up_recommended=follow_up,
    )
    db.add(row)
    db.commit()

    return row


class TestWindow:
    def test_only_messages_inside_the_period_are_counted(
        self, db_session: Session, test_user: User
    ):
        provider = RecordingProvider(
            messages=[
                message(message_id="in", received_at=INSIDE),
                message(message_id="out", received_at=BEFORE),
            ]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert digest.received_count == 1

    def test_a_message_with_no_date_is_counted_in_no_period(
        self, db_session: Session, test_user: User
    ):
        # Sweeping it into whichever window is being generated would invent a
        # date the provider never gave.
        # Built directly rather than through `message()`, whose `received_at`
        # default would fill in a date and hide the case under test.
        provider = RecordingProvider(
            messages=[EmailMessage(message_id="undated", subject="No date")]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert digest.received_count == 0
        assert digest.undated_count == 1

    def test_the_boundary_belongs_to_exactly_one_period(
        self, db_session: Session, test_user: User
    ):
        provider = RecordingProvider(
            messages=[
                message(message_id="opens", received_at=WEEK.start),
                message(message_id="closes", received_at=WEEK.end),
            ]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert digest.received_count == 1


class TestTriage:
    def test_an_untriaged_message_is_reported_as_untriaged_not_guessed(
        self, db_session: Session, test_user: User
    ):
        # Classifying it here would be a model call per message, silently, on
        # a schedule.
        provider = RecordingProvider(
            messages=[message(message_id="m1", received_at=INSIDE)]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert digest.untriaged_count == 1
        assert digest.triaged_count == 0
        assert digest.by_category == {"untriaged": 1}

    def test_a_triaged_message_carries_the_verdict_that_was_stored(
        self, db_session: Session, test_user: User
    ):
        triaged(db_session, user=test_user, message_id="m1", follow_up=True)
        provider = RecordingProvider(
            messages=[message(message_id="m1", received_at=INSIDE)]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert digest.triaged_count == 1
        assert digest.needs_reply_count == 1
        assert digest.follow_up_count == 1
        assert digest.needs_reply[0].summary is not None

    def test_another_persons_assessment_is_never_read(
        self, db_session: Session, test_user: User, test_user_b: User
    ):
        # Same provider message id, different owner. Triage is private.
        triaged(db_session, user=test_user_b, message_id="m1")
        provider = RecordingProvider(
            messages=[message(message_id="m1", received_at=INSIDE)]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert digest.untriaged_count == 1
        assert digest.needs_reply_count == 0

    def test_highlights_repeat_triages_opinion_rather_than_forming_one(
        self, db_session: Session, test_user: User
    ):
        triaged(
            db_session,
            user=test_user,
            message_id="loud",
            category=EmailCategory.URGENT,
            priority=EmailPriority.HIGH,
        )
        triaged(
            db_session,
            user=test_user,
            message_id="quiet",
            category=EmailCategory.FYI,
            priority=EmailPriority.LOW,
        )

        provider = RecordingProvider(
            messages=[
                message(message_id="loud", received_at=INSIDE),
                message(message_id="quiet", received_at=INSIDE),
            ]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert [item.message_id for item in digest.highlights] == ["loud"]


class TestSent:
    def test_only_a_draft_the_provider_actually_sent_counts(
        self, db_session: Session, test_user: User
    ):
        for status, sent_at in (
            (EmailDraftStatus.SENT, INSIDE),
            (EmailDraftStatus.SENT, BEFORE),
            (EmailDraftStatus.APPROVED, None),
        ):
            db_session.add(
                EmailDraft(
                    user_id=test_user.id,
                    subject="Revised numbers",
                    body="Attached.",
                    to_recipients=["client@example.com"],
                    status=status,
                    sent_at=sent_at,
                )
            )
        db_session.commit()

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=RecordingProvider(messages=[]),
        )

        assert digest.sent_count == 1


class TestCorrespondents:
    def test_senders_are_ranked_by_how_much_of_the_period_they_account_for(
        self, db_session: Session, test_user: User
    ):
        provider = RecordingProvider(
            messages=[
                message(message_id="a", sender="loud@example.com", received_at=INSIDE),
                message(message_id="b", sender="loud@example.com", received_at=INSIDE),
                message(message_id="c", sender="rare@example.com", received_at=INSIDE),
            ]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=provider,
        )

        assert [person.address for person in digest.top_correspondents] == [
            "loud@example.com",
            "rare@example.com",
        ]
        assert digest.top_correspondents[0].message_count == 2


class TestHonesty:
    def test_no_mailbox_refuses_rather_than_returning_zeroes(
        self, db_session: Session, test_user: User
    ):
        # Zero counts would assert that a mailbox was read and held nothing.
        # Nothing was read.
        with pytest.raises(EmailProviderNotConfiguredError):
            digest_service.build(db_session, user_id=test_user.id, period=WEEK, now=NOW)

    def test_a_genuinely_quiet_week_is_distinguishable_from_a_refusal(
        self, db_session: Session, test_user: User
    ):
        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            provider=RecordingProvider(messages=[]),
        )

        assert digest.is_quiet is True
        assert digest.received_count == 0

    def test_a_full_page_still_inside_the_window_is_reported_as_truncated(
        self, db_session: Session, test_user: User
    ):
        # The provider boundary offers no date range, so a bounded page can cut
        # the window short. A partial count that says so is useful; one that
        # does not is a wrong total.
        provider = RecordingProvider(
            messages=[
                message(message_id=f"m{index}", received_at=INSIDE)
                for index in range(3)
            ]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            limit=3,
            provider=provider,
        )

        assert digest.truncated is True
        assert digest.truncation_detail is not None

    def test_a_page_that_reaches_past_the_period_is_not_truncated(
        self, db_session: Session, test_user: User
    ):
        # The oldest message fetched predates the window, so everything in the
        # window was seen even though the page was full.
        provider = RecordingProvider(
            messages=[
                message(message_id="in", received_at=INSIDE),
                message(message_id="older", received_at=BEFORE),
            ]
        )

        digest = digest_service.build(
            db_session,
            user_id=test_user.id,
            period=WEEK,
            now=NOW,
            limit=2,
            provider=provider,
        )

        assert digest.truncated is False
