"""The Reports endpoints: identity, periods, and what history means over HTTP.

The isolation assertions here are the ones that matter most. `X-User-ID` is a
claim rather than a proof, and the guarantee the MVP does make is that a
request carrying one person's id can never read another person's report — which
is only worth anything if it is tested at the edge, not only in the service.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.report import ReportStatus, ReportType
from app.models.task import Task
from app.models.user import User
from app.services.features.reports import report_store
from app.services.features.reports.period import current, month_of, week_of
from app.services.features.tasks.urgency import TaskPriority, TaskStatus

NOW = datetime.now(UTC)
LAST_WEEK = week_of(NOW - timedelta(days=7))


def add_task(db: Session, *, user: User, title: str) -> Task:
    task = Task(
        user_id=user.id,
        title=title,
        status=TaskStatus.TODO,
        priority=TaskPriority.NORMAL,
        due_at=NOW + timedelta(days=1),
        source="manual",
    )
    db.add(task)
    db.commit()

    return task


class TestIdentity:
    def test_a_request_with_no_identity_is_refused(self, client: TestClient):
        assert client.get("/reports").status_code == 401

    def test_an_identity_that_is_not_a_uuid_is_refused(self, client: TestClient):
        response = client.get("/reports", headers={"X-User-ID": "the-ceo"})

        assert response.status_code == 422

    def test_an_identity_naming_nobody_is_refused(self, client: TestClient):
        response = client.get(
            "/reports",
            headers={"X-User-ID": "00000000-0000-0000-0000-000000000000"},
        )

        assert response.status_code == 404


class TestIsolation:
    def test_one_persons_report_never_contains_anothers_work(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
    ):
        add_task(db_session, user=test_user_b, title="Their confidential task")

        response = client.get("/reports", headers=user_headers)

        assert response.status_code == 200
        assert "Their confidential task" not in response.text

    def test_history_is_scoped_to_the_caller(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
    ):
        report_store.record(
            db_session,
            user_id=test_user_b.id,
            report_type=ReportType.WEEKLY_WORK,
            period=LAST_WEEK,
            content={"headline": "theirs"},
        )

        response = client.get("/reports/history", headers=user_headers)

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_switching_identity_switches_the_report(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
    ):
        add_task(db_session, user=test_user, title="Mine")
        add_task(db_session, user=test_user_b, title="Theirs")

        mine = client.get("/reports", headers=user_headers).json()
        theirs = client.get("/reports", headers=user_b_headers).json()

        assert "Mine" in str(mine["content"])
        assert "Theirs" not in str(mine["content"])
        assert "Theirs" in str(theirs["content"])


class TestPeriods:
    def test_the_default_is_the_period_in_progress(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        body = client.get("/reports", headers=user_headers).json()

        assert (
            body["period"]["key"]
            == current(report_store.period_kind(ReportType.WEEKLY_WORK)).key
        )
        assert body["period"]["is_complete"] is False
        assert body["is_provisional"] is True

    def test_a_week_key_that_is_not_a_monday_is_a_400_not_a_silent_swap(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # Answering with a different week than the one asked for is the one
        # failure mode a report must not have.
        response = client.get(
            "/reports", params={"period": "2026-09-02"}, headers=user_headers
        )

        assert response.status_code == 400
        assert "Monday" in response.json()["detail"]

    def test_nonsense_is_a_400(self, client: TestClient, user_headers: dict[str, str]):
        response = client.get(
            "/reports", params={"period": "last-week"}, headers=user_headers
        )

        assert response.status_code == 400

    def test_a_month_key_selects_a_month_for_the_monthly_digest(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        response = client.get(
            "/reports",
            params={
                "report_type": ReportType.MONTHLY_EMAIL_DIGEST.value,
                "period": "2026-08",
            },
            headers=user_headers,
        )

        assert response.status_code == 200
        assert response.json()["period"]["kind"] == "month"

    def test_an_unknown_report_type_is_rejected_by_validation(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        response = client.get(
            "/reports", params={"report_type": "quarterly"}, headers=user_headers
        )

        assert response.status_code == 422


class TestHistory:
    def test_the_selector_is_populated_before_anything_is_stored(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # A selector driven only by stored rows would be empty on a person's
        # first ever visit, and there would be no way to ask for last week.
        body = client.get("/reports/history", headers=user_headers).json()

        assert body["items"] == []
        assert len(body["available_periods"]) > 1
        assert body["available_periods"][0]["is_complete"] is False
        assert body["available_periods"][1]["is_complete"] is True

    def test_a_stored_report_appears_in_history_with_its_status(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_EMAIL_DIGEST,
            period=LAST_WEEK,
            content={},
            status=ReportStatus.UNAVAILABLE,
            detail="No mailbox is connected for this user.",
        )

        body = client.get(
            "/reports/history",
            params={"report_type": ReportType.WEEKLY_EMAIL_DIGEST.value},
            headers=user_headers,
        ).json()

        assert body["total"] == 1
        assert body["items"][0]["status"] == "unavailable"
        assert "mailbox" in body["items"][0]["detail"].lower()

    def test_history_of_one_type_excludes_the_others(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=LAST_WEEK,
            content={},
        )

        body = client.get(
            "/reports/history",
            params={"report_type": ReportType.MONTHLY_EMAIL_DIGEST.value},
            headers=user_headers,
        ).json()

        assert body["items"] == []


class TestWorkReport:
    def test_a_closed_week_is_answered_from_the_store_and_does_not_move(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        first = client.get(
            "/reports", params={"period": LAST_WEEK.key}, headers=user_headers
        ).json()

        assert first["from_history"] is False
        assert first["is_provisional"] is False

        # The work moves on...
        add_task(db_session, user=test_user, title="Added afterwards")

        second = client.get(
            "/reports",
            params={"period": LAST_WEEK.key, "refresh": "true"},
            headers=user_headers,
        ).json()

        # ...and the report of that closed week does not.
        assert second["from_history"] is True
        assert "Added afterwards" not in str(second["content"])

    def test_the_running_week_reflects_work_added_since(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        client.get("/reports", headers=user_headers)
        add_task(db_session, user=test_user, title="Just added")

        body = client.get(
            "/reports", params={"refresh": "true"}, headers=user_headers
        ).json()

        assert "Just added" in str(body["content"])

    def test_the_body_is_the_same_shape_the_weekly_endpoint_serves(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # One reader in the frontend, not two. The envelope says when; the
        # content is the report either endpoint would return.
        envelope = client.get("/reports", headers=user_headers).json()
        direct = client.get("/reports/weekly", headers=user_headers).json()

        assert set(envelope["content"]) == set(direct)


class TestDigests:
    def test_a_digest_without_a_mailbox_is_unavailable_not_empty(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        body = client.get(
            "/reports",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_headers,
        ).json()

        assert body["status"] == "unavailable"
        assert body["detail"]
        # No counted email activity...
        assert "received_count" not in body["content"]
        # ...but the report still exists, because tasks do not need a mailbox.
        assert body["content"]["activity"]["sections"]

    def test_running_the_digest_job_reports_what_it_produced(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        response = client.post("/reports/digests/run", headers=user_headers)

        assert response.status_code == 200
        body = response.json()

        assert body["total"] == 2
        assert {item["report_type"] for item in body["items"]} == {
            "weekly_email_digest",
            "monthly_email_digest",
        }

    def test_the_digest_job_only_ever_runs_for_the_caller(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
    ):
        client.post("/reports/digests/run", headers=user_headers)

        assert report_store.history(db_session, user_id=test_user_b.id) == []

    @pytest.mark.parametrize(
        ("report_type", "period"),
        [
            (ReportType.WEEKLY_EMAIL_DIGEST, LAST_WEEK),
            (
                ReportType.MONTHLY_EMAIL_DIGEST,
                month_of(NOW.replace(day=1) - timedelta(days=1)),
            ),
        ],
    )
    def test_both_digests_answer_over_their_own_rhythm(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        report_type: ReportType,
        period,
    ):
        body = client.get(
            "/reports",
            params={"report_type": report_type.value, "period": period.key},
            headers=user_headers,
        ).json()

        assert body["period"]["key"] == period.key
        assert body["report_type"] == report_type.value


class TestPeriodActuallySelects:
    """The bug this class exists for: every period showed today's work.

    The route built the report from the live clock whatever period was asked
    for, so last week, the week before, and the week in progress all rendered
    the same tasks — and then each was written down under its own key as though
    it were that week's history.
    """

    def _task(
        self,
        db: Session,
        *,
        user: User,
        title: str,
        created_at: datetime,
        completed_at: datetime | None = None,
        due_at: datetime | None = None,
    ) -> Task:
        task = Task(
            user_id=user.id,
            title=title,
            status=TaskStatus.COMPLETED if completed_at else TaskStatus.TODO,
            priority=TaskPriority.NORMAL,
            due_at=due_at,
            completed_at=completed_at,
            source="manual",
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(task)
        db.commit()

        return task

    def test_work_created_this_week_does_not_appear_in_last_weeks_report(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        self._task(
            db_session,
            user=test_user,
            title="Created this morning",
            created_at=NOW,
        )

        body = client.get(
            "/reports", params={"period": LAST_WEEK.key}, headers=user_headers
        ).json()

        assert "Created this morning" not in str(body["content"])

    def test_a_task_completed_after_the_period_reads_as_open_within_it(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        # `status` is only ever current. Asking what last week looked like has
        # to un-ask today's completion, or every past report shows the work as
        # already done and nothing was ever outstanding.
        self._task(
            db_session,
            user=test_user,
            title="Finished since",
            created_at=LAST_WEEK.start,
            due_at=LAST_WEEK.start + timedelta(days=1),
            completed_at=NOW,
        )

        last = client.get(
            "/reports", params={"period": LAST_WEEK.key}, headers=user_headers
        ).json()["content"]

        assert [item["title"] for item in last["overdue"]] == ["Finished since"]
        assert last["completed"] == []

        this = client.get("/reports", headers=user_headers).json()["content"]

        assert [item["title"] for item in this["completed"]] == ["Finished since"]

    def test_a_completion_belongs_to_the_period_it_happened_in(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        long_ago = week_of(NOW - timedelta(days=60))
        self._task(
            db_session,
            user=test_user,
            title="Old news",
            created_at=long_ago.start,
            completed_at=long_ago.start + timedelta(days=1),
        )

        last = client.get(
            "/reports", params={"period": LAST_WEEK.key}, headers=user_headers
        ).json()["content"]

        # Finished two months ago: not an achievement of last week.
        assert last["completed"] == []
        assert last["completed_count"] == 0

        its_own = client.get(
            "/reports", params={"period": long_ago.key}, headers=user_headers
        ).json()["content"]

        assert [item["title"] for item in its_own["completed"]] == ["Old news"]

    def test_a_week_with_no_activity_is_genuinely_empty(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        self._task(db_session, user=test_user, title="Only now", created_at=NOW)

        quiet = week_of(NOW - timedelta(days=90))
        body = client.get(
            "/reports", params={"period": quiet.key}, headers=user_headers
        ).json()["content"]

        assert body["overdue"] == []
        assert body["upcoming"] == []
        assert body["completed"] == []
        assert body["overdue_count"] == 0
        assert body["escalation_count"] == 0

    def test_the_period_is_reported_as_the_one_that_was_asked_for(
        self,
        client: TestClient,
        user_headers: dict[str, str],
    ):
        # The report's own `period_start`/`period_end` must describe the week
        # selected, not a window around today.
        body = client.get(
            "/reports", params={"period": LAST_WEEK.key}, headers=user_headers
        ).json()

        assert body["period"]["key"] == LAST_WEEK.key
        assert body["content"]["period_start"].startswith(
            LAST_WEEK.start.date().isoformat()
        )


class TestSelectorIsUseful:
    def test_only_the_running_and_previous_period_are_offered_by_default(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # Twelve options, eleven of them guaranteed empty, made "previous week"
        # hard to find. This system has no history before it was deployed.
        body = client.get("/reports/history", headers=user_headers).json()

        assert len(body["available_periods"]) == 2
        assert body["available_periods"][0]["is_complete"] is False
        assert body["available_periods"][1]["is_complete"] is True

    def test_a_generated_report_keeps_its_period_reachable(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        old = week_of(NOW - timedelta(days=45))
        report_store.record(
            db_session,
            user_id=test_user.id,
            report_type=ReportType.WEEKLY_WORK,
            period=old,
            content={},
        )

        body = client.get("/reports/history", headers=user_headers).json()
        keys = [period["key"] for period in body["available_periods"]]

        assert old.key in keys
        assert keys == sorted(keys, reverse=True)


class TestActivityReports:
    """The two digests are Activity Reports: what happened, as a document."""

    def test_a_report_carries_the_readable_sections(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        add_task(db_session, user=test_user, title="Draft the proposal")

        body = client.get(
            "/reports",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_headers,
        ).json()

        activity = body["content"]["activity"]
        titles = [section["title"] for section in activity["sections"]]

        assert "Executive summary" in titles
        assert "Bottom line" in titles
        assert activity["period_label"] == LAST_WEEK.label
        assert activity["user_name"] == test_user.name

    def test_nothing_is_invented_when_there_is_no_mailbox(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        body = client.get(
            "/reports",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_headers,
        ).json()

        rendered = str(body["content"]["activity"])

        # It says which half is missing rather than reporting zero messages,
        # which would claim a mailbox was read.
        assert "no mailbox was connected" in rendered.lower()
        assert "calendar" in rendered.lower()

    def test_a_generated_report_downloads_as_a_word_document(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        client.get(
            "/reports",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_headers,
        )

        response = client.get(
            "/reports/document",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_headers,
        )

        assert response.status_code == 200
        assert "wordprocessingml" in response.headers["content-type"]
        assert LAST_WEEK.key in response.headers["content-disposition"]
        # A real zip container, not an empty body with a hopeful content type.
        assert response.content[:2] == b"PK"

    def test_a_period_nobody_generated_is_not_downloadable(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # Producing one on the way out would put a report in somebody's
        # downloads folder that appears in no history and nobody chose to make.
        never = week_of(NOW - timedelta(days=120))

        refused = client.get(
            "/reports/document",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": never.key,
            },
            headers=user_headers,
        )

        assert refused.status_code == 400
        assert "generated" in refused.json()["detail"]

    def test_a_download_never_reaches_another_persons_report(
        self,
        client: TestClient,
        db_session: Session,
        test_user_b: User,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
    ):
        client.get(
            "/reports",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_b_headers,
        )

        mine = client.get(
            "/reports/document",
            params={
                "report_type": ReportType.WEEKLY_EMAIL_DIGEST.value,
                "period": LAST_WEEK.key,
            },
            headers=user_headers,
        )

        assert mine.status_code == 400


class TestHistoryIsShort:
    def test_history_returns_at_most_a_handful(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        # An unbounded list turns the Reports page into an archive index.
        for weeks_back in range(1, 10):
            report_store.record(
                db_session,
                user_id=test_user.id,
                report_type=ReportType.WEEKLY_WORK,
                period=week_of(NOW - timedelta(weeks=weeks_back)),
                content={},
            )

        body = client.get("/reports/history", headers=user_headers).json()

        assert len(body["items"]) == 5
        # Newest first, so the five are the five most recent.
        keys = [item["period"]["key"] for item in body["items"]]
        assert keys == sorted(keys, reverse=True)

    def test_nothing_generated_means_nothing_listed(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # The empty state is "no reports yet", not a column of blank months.
        body = client.get("/reports/history", headers=user_headers).json()

        assert body["items"] == []
        assert body["total"] == 0
