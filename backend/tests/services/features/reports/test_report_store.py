"""The one guarantee: a report of a closed period never changes.

Everything the Reports workspace shows about history rests on this. If a stored
report can be silently rewritten, "what did the last week of August say" has no
answer, and the whole persistence layer is an expensive cache.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.models.report import ReportStatus, ReportType
from app.models.user import User
from app.services.features.reports import report_store
from app.services.features.reports.period import PeriodKind, month_of, week_of

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

CLOSED = week_of(datetime(2026, 8, 26, tzinfo=UTC))
RUNNING = week_of(NOW)


class TestFinality:
    def test_a_closed_period_is_written_once_and_kept(
        self, db_session: Session, test_user: User
    ):
        first = report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=CLOSED,
            content={"headline": "as it was"},
            now=NOW,
        )

        second = report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=CLOSED,
            content={"headline": "rewritten later"},
            now=NOW,
        )

        assert second.id == first.id
        assert second.content == {"headline": "as it was"}
        assert second.is_provisional is False

    def test_a_running_period_is_replaced_in_place(
        self, db_session: Session, test_user: User
    ):
        # The week is not over. Its numbers will change, and asking again
        # should show the current state rather than a stale one.
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=RUNNING,
            content={"overdue": 1},
            now=NOW,
        )

        later = report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=RUNNING,
            content={"overdue": 3},
            now=NOW,
        )

        assert later.content == {"overdue": 3}
        assert later.is_provisional is True

    def test_a_provisional_row_becomes_final_once_the_period_closes(
        self, db_session: Session, test_user: User
    ):
        # Generated mid-week...
        mid_week = RUNNING.start
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=RUNNING,
            content={"partial": True},
            now=mid_week,
        )

        # ...and again after the week has ended. That write is the last one.
        after = RUNNING.end
        final = report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=RUNNING,
            content={"partial": False},
            now=after,
        )

        assert final.is_provisional is False

        frozen = report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=RUNNING,
            content={"partial": "changed again"},
            now=after,
        )

        assert frozen.content == {"partial": False}

    def test_is_final_reads_the_clock_and_nothing_else(self):
        assert report_store.is_final(CLOSED, NOW)
        assert not report_store.is_final(RUNNING, NOW)


class TestIsolation:
    def test_two_people_hold_separate_reports_for_the_same_week(
        self, db_session: Session, test_user: User, test_user_b: User
    ):
        for user, headline in ((test_user, "mine"), (test_user_b, "theirs")):
            report_store.record(
                db_session,
                user_id=user.id,
                report_type=ReportType.WEEKLY_WORK,
                period=CLOSED,
                content={"headline": headline},
                now=NOW,
            )

        mine = report_store.find(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=CLOSED,
        )

        assert mine is not None
        assert mine.content == {"headline": "mine"}

        assert len(report_store.history(db_session, user_id=test_user.id)) == 1
        assert len(report_store.history(db_session, user_id=test_user_b.id)) == 1

    def test_history_never_returns_another_persons_rows(
        self, db_session: Session, test_user: User, test_user_b: User
    ):
        report_store.record(
            db_session,
            user_id=test_user_b.id,
            report_type=ReportType.MONTHLY_EMAIL_DIGEST,
            period=month_of(datetime(2026, 7, 15, tzinfo=UTC)),
            content={},
            now=NOW,
        )

        assert report_store.history(db_session, user_id=test_user.id) == []


class TestHistory:
    def test_three_types_of_the_same_week_are_three_rows(
        self, db_session: Session, test_user: User
    ):
        # The unique constraint is on (user, type, period), so the work report
        # and the weekly digest for one week coexist.
        for report_type in (
            ReportType.WEEKLY_WORK,
            ReportType.WEEKLY_EMAIL_DIGEST,
        ):
            report_store.record(
                db_session,
                user_id=test_user.id,
                report_type=report_type,
                period=CLOSED,
                content={},
                now=NOW,
            )

        assert len(report_store.history(db_session, user_id=test_user.id)) == 2

    def test_history_is_newest_period_first(self, db_session: Session, test_user: User):
        weeks = [
            week_of(datetime(2026, 8, 5, tzinfo=UTC)),
            week_of(datetime(2026, 8, 19, tzinfo=UTC)),
            week_of(datetime(2026, 8, 12, tzinfo=UTC)),
        ]

        for period in weeks:
            report_store.record(
                db_session,
                user_id=test_user.id,
                report_type=ReportType.WEEKLY_WORK,
                period=period,
                content={},
                now=NOW,
            )

        keys = [
            row.period_start.date().isoformat()
            for row in report_store.history(db_session, user_id=test_user.id)
        ]

        assert keys == ["2026-08-17", "2026-08-10", "2026-08-03"]

    def test_history_can_be_filtered_to_one_type(
        self, db_session: Session, test_user: User
    ):
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=CLOSED,
            content={},
            now=NOW,
        )
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            content={},
            now=NOW,
        )

        rows = report_store.history(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
        )

        assert [row.report_type for row in rows] == [ReportType.WEEKLY_EMAIL_DIGEST]


class TestUnavailability:
    def test_could_not_be_produced_is_a_different_row_from_nothing_happened(
        self, db_session: Session, test_user: User
    ):
        # The distinction the whole feature turns on. An empty digest claims
        # the mailbox was read; this one records that it was not.
        row = report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=CLOSED,
            content={},
            status=ReportStatus.UNAVAILABLE,
            detail="No mailbox is connected for this user.",
            now=NOW,
        )

        assert row.status is ReportStatus.UNAVAILABLE
        assert row.content == {}
        assert "No mailbox" in (row.detail or "")


class TestPeriodKinds:
    @pytest.mark.parametrize(
        ("report_type", "kind"),
        [
            (ReportType.WEEKLY_WORK, PeriodKind.WEEK),
            (ReportType.WEEKLY_EMAIL_DIGEST, PeriodKind.WEEK),
            (ReportType.MONTHLY_EMAIL_DIGEST, PeriodKind.MONTH),
        ],
    )
    def test_every_report_type_declares_its_rhythm(self, report_type, kind):
        assert report_store.period_kind(report_type) is kind

    def test_a_new_report_type_cannot_be_added_without_deciding_one(self):
        # The map is exhaustive by test rather than by type checker: a member
        # added to the enum and not to PERIOD_KIND would fail here rather than
        # raising a KeyError in production the first time somebody selected it.
        assert set(report_store.PERIOD_KIND) == set(ReportType)
