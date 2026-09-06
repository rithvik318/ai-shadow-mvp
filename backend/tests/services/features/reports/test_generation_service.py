"""Generating a digest once, and what happens on every request after that.

The scheduler and the endpoint call the same function, so these tests stand in
for both. What they check is mostly negative: that the provider is *not* asked
again, that a refusal is recorded rather than dressed up as an empty week, and
that one user's failure does not take the run down.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import EmailProviderAuthError
from app.models.report import ReportStatus, ReportType
from app.models.user import User
from app.services.features.reports import (
    digest_service,
    generation_service,
    report_store,
)
from app.services.features.reports.period import month_of, week_of
from tests.support.email import RecordingProvider, message

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
CLOSED = week_of(datetime(2026, 8, 26, tzinfo=UTC))
RUNNING = week_of(NOW)
INSIDE = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


class CountingProvider(RecordingProvider):
    """Records how many times the mailbox was actually read."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.reads = 0

    def list_messages(self, *, limit: int = 25, folder: str | None = None):
        self.reads += 1
        return super().list_messages(limit=limit, folder=folder)


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> CountingProvider:
    """A mailbox every user in the test resolves to."""

    provider = CountingProvider(messages=[message(message_id="m1", received_at=INSIDE)])

    monkeypatch.setattr(
        "app.services.features.email.mailbox_config_service.provider_for",
        lambda db, *, user_id: provider,
    )
    monkeypatch.setattr(
        "app.services.features.email.mailbox_config_service.resolve_address",
        lambda db, *, user_id: "person@sunradia.com",
    )

    return provider


class TestFinality:
    def test_a_closed_period_is_read_back_without_touching_the_mailbox(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        first = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        assert first.from_history is False
        assert connected.reads == 1

        again = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        assert again.from_history is True
        assert again.content == first.content
        # The whole point: a claim about mail that already happened is not
        # re-derived from a mailbox that has moved on since.
        assert connected.reads == 1

    def test_refresh_cannot_rewrite_a_closed_period(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        result = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
            refresh=True,
        )

        assert result.from_history is True
        assert connected.reads == 1

    def test_a_running_period_is_rebuilt_when_refresh_is_asked_for(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=RUNNING,
            now=NOW,
        )

        generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=RUNNING,
            now=NOW,
            refresh=True,
        )

        assert connected.reads == 2

    def test_a_running_period_is_served_from_the_row_without_refresh(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        for _ in range(3):
            generation_service.generate_digest(
                db_session,
                user_id=test_user.id,
                report_type=ReportType.WEEKLY_EMAIL_DIGEST,
                period=RUNNING,
                now=NOW,
            )

        assert connected.reads == 1


class TestRefusals:
    def test_no_mailbox_is_recorded_as_unavailable_with_a_reason(
        self, db_session: Session, test_user: User
    ):
        result = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        assert result.status is ReportStatus.UNAVAILABLE
        assert "mailbox" in (result.detail or "").lower()

        # No email figures — nothing was read, so there is nothing to count.
        assert "received_count" not in result.content

        # But the task half of the period is real and does not depend on a
        # mailbox, so the report is still produced and says which half is
        # missing rather than reporting the period as nothing.
        activity = result.content["activity"]
        assert activity["sections"]
        assert "no mailbox was connected" in str(activity).lower()

        stored = report_store.find(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
        )

        assert stored is not None
        assert stored.status is ReportStatus.UNAVAILABLE

    def test_a_provider_that_refuses_is_recorded_not_swallowed(
        self, db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
    ):
        def refuse(*args, **kwargs):
            raise EmailProviderAuthError("Mail.Read has not been consented.")

        monkeypatch.setattr(digest_service, "build", refuse)

        result = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.MONTHLY_EMAIL_DIGEST,
            period=month_of(datetime(2026, 8, 15, tzinfo=UTC)),
            now=NOW,
        )

        assert result.status is ReportStatus.UNAVAILABLE
        assert "consented" in (result.detail or "")

    def test_a_period_that_has_not_started_is_not_a_mailbox_call(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        future = week_of(datetime(2026, 12, 7, tzinfo=UTC))

        result = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=future,
            now=NOW,
        )

        assert result.status is ReportStatus.UNAVAILABLE
        assert connected.reads == 0
        # Nothing is written down either: the future is not history.
        assert (
            report_store.find(
                db_session,
                user_id=test_user.id,
                report_type=ReportType.WEEKLY_EMAIL_DIGEST,
                period=future,
            )
            is None
        )

    def test_the_work_report_is_not_a_digest(
        self, db_session: Session, test_user: User
    ):
        with pytest.raises(ValueError, match="not an email digest"):
            generation_service.generate_digest(
                db_session,
                user_id=test_user.id,
                report_type=ReportType.WEEKLY_WORK,
                period=CLOSED,
                now=NOW,
            )


class TestScheduledRun:
    def test_a_run_snapshots_the_last_closed_week_and_month_for_each_user(
        self,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        connected: CountingProvider,
    ):
        results = generation_service.generate_due_digests(
            db_session, user_ids=[test_user.id, test_user_b.id], now=NOW
        )

        assert len(results) == 4
        assert {result.report_type for result in results} == set(
            generation_service.DIGEST_TYPES
        )

        for result in results:
            assert result.period.end <= NOW
            assert result.is_provisional is False

    def test_running_the_job_again_costs_no_mailbox_calls(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        generation_service.generate_due_digests(
            db_session, user_ids=[test_user.id], now=NOW
        )
        reads_after_first = connected.reads

        generation_service.generate_due_digests(
            db_session, user_ids=[test_user.id], now=NOW
        )

        assert connected.reads == reads_after_first

    def test_one_users_failure_does_not_abandon_the_rest(
        self,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        monkeypatch: pytest.MonkeyPatch,
        connected: CountingProvider,
    ):
        # A scheduled job that gives up on forty people over one bad mailbox is
        # worse than one that reports forty-one outcomes.
        failing = test_user.id

        original = generation_service.generate_digest

        def sometimes(db, *, user_id, **kwargs):
            if user_id == failing:
                raise RuntimeError("something unforeseen")

            return original(db, user_id=user_id, **kwargs)

        monkeypatch.setattr(generation_service, "generate_digest", sometimes)

        results = generation_service.generate_due_digests(
            db_session, user_ids=[test_user.id, test_user_b.id], now=NOW
        )

        assert len(results) == 2
        assert {result.report_type for result in results} == set(
            generation_service.DIGEST_TYPES
        )


class TestRecordedRefusalsAreRetried:
    """Connecting a mailbox on Tuesday must not leave last week blank forever.

    A stored *report* for a closed period is immutable. A stored refusal is
    not: it records that nothing could be read, and the ordinary sequence is
    that somebody then connects a mailbox and asks again. Treating the two the
    same made the second half of that sequence impossible.
    """

    def test_a_refusal_is_replaced_once_a_mailbox_exists(
        self, db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
    ):
        first = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        assert first.status is ReportStatus.UNAVAILABLE

        provider = CountingProvider(
            messages=[message(message_id="m1", received_at=INSIDE)]
        )
        monkeypatch.setattr(
            "app.services.features.email.mailbox_config_service.provider_for",
            lambda db, *, user_id: provider,
        )
        monkeypatch.setattr(
            "app.services.features.email.mailbox_config_service.resolve_address",
            lambda db, *, user_id: "person@sunradia.com",
        )

        second = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        assert second.status is ReportStatus.COMPLETE
        assert second.content["received_count"] == 1
        assert provider.reads == 1

    def test_a_real_report_is_still_never_rewritten(
        self, db_session: Session, test_user: User, connected: CountingProvider
    ):
        # The guarantee the exception must not erode.
        generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        again = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
            refresh=True,
        )

        assert again.from_history is True
        assert connected.reads == 1

    def test_a_refusal_that_still_refuses_keeps_its_original_row(
        self, db_session: Session, test_user: User
    ):
        first = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )
        again = generation_service.generate_digest(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            now=NOW,
        )

        # Nothing was retried successfully, so nothing pretends it was.
        assert again.generated_at == first.generated_at
