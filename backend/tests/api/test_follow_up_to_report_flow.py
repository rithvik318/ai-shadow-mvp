"""Email → follow-up → task → weekly report, over HTTP, as one person.

The pieces of this chain each have their own tests. This module tests the
*seam*: that a follow-up triage recommended becomes a task the caller owns,
that the task carries the deadline the message stated and no other, and that
the weekly report the frontend reads actually contains it.

It runs through the API rather than the services on purpose. Every link in the
chain is user-scoped, and the guarantee that matters — that none of it crosses
between two people — is only worth anything if it holds at the edge, where the
identity header is the only thing saying who is asking.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.email import EmailAssessment, EmailCategory, EmailPriority
from app.models.report import ReportType
from app.models.user import User

NOW = datetime.now(UTC)


def follow_up(
    db: Session,
    *,
    user: User,
    message_id: str = "AAMkAG-1",
    subject: str = "Revised proposal numbers",
    due_at: datetime | None = None,
    priority: EmailPriority = EmailPriority.HIGH,
    recommended: bool = True,
    handled: bool = False,
) -> EmailAssessment:
    """One triaged message, as triage would have stored it."""

    row = EmailAssessment(
        user_id=user.id,
        provider="outlook",
        provider_message_id=message_id,
        provider_thread_id="thread-1",
        subject=subject,
        sender_name="Robert Keenan",
        sender_address="robert.keenan@example.com",
        received_at=NOW - timedelta(days=2),
        category=EmailCategory.FOLLOW_UP,
        priority=priority,
        summary="They are waiting on the revised numbers.",
        follow_up_recommended=recommended,
        follow_up_reason="They asked twice and have had no answer.",
        follow_up_due_at=due_at,
        handled=handled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return row


class TestTheChain:
    def test_a_follow_up_becomes_a_task_that_reaches_the_weekly_report(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        due = NOW - timedelta(days=9)
        follow_up(db_session, user=test_user, due_at=due)

        created = client.post("/tasks/from-follow-ups", headers=user_headers)

        assert created.status_code == 200
        assert created.json()["total"] == 1

        task = created.json()["items"][0]
        assert task["title"] == "Follow up: Revised proposal numbers"
        assert task["source"] == "email_follow_up"
        assert task["contact_address"] == "robert.keenan@example.com"

        report = client.get("/reports/weekly", headers=user_headers)

        assert report.status_code == 200
        body = report.json()

        # Overdue by nine days on a high-priority task: the report should place
        # it in overdue, count it, and ask for it to be escalated.
        assert [item["title"] for item in body["overdue"]] == [task["title"]]
        assert body["overdue_count"] == 1
        assert body["escalation_count"] == 1
        assert [item["title"] for item in body["follow_ups"]] == [task["title"]]
        assert [item["title"] for item in body["high_priority"]] == [task["title"]]

    def test_the_same_task_appears_in_the_reports_envelope(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        # The Reports workspace reads the envelope; the interactive weekly
        # screen reads /reports/weekly. Both must show the same work.
        follow_up(db_session, user=test_user, due_at=NOW + timedelta(days=1))
        client.post("/tasks/from-follow-ups", headers=user_headers)

        envelope = client.get(
            "/reports",
            params={"report_type": ReportType.WEEKLY_WORK.value},
            headers=user_headers,
        ).json()

        assert "Follow up: Revised proposal numbers" in str(envelope["content"])

    def test_a_deadline_appears_only_when_the_message_gave_one(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        # The refusal that matters most in this chain. Inventing a due date
        # would drive an amber badge, and eventually an escalation, off a date
        # nobody ever stated.
        follow_up(db_session, user=test_user, message_id="undated", due_at=None)

        task = client.post("/tasks/from-follow-ups", headers=user_headers).json()[
            "items"
        ][0]

        assert task["due_at"] is None
        assert task["is_overdue"] is False
        assert task["urgency"] == "normal"


class TestIdempotence:
    def test_pressing_it_twice_does_not_produce_two_tasks(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user)

        first = client.post("/tasks/from-follow-ups", headers=user_headers).json()
        second = client.post("/tasks/from-follow-ups", headers=user_headers).json()

        assert first["total"] == 1
        assert second["total"] == 1
        assert first["items"][0]["id"] == second["items"][0]["id"]

    def test_it_returns_the_state_rather_than_the_diff(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user, message_id="one", subject="First")
        client.post("/tasks/from-follow-ups", headers=user_headers)

        follow_up(db_session, user=test_user, message_id="two", subject="Second")
        second = client.post("/tasks/from-follow-ups", headers=user_headers).json()

        assert second["total"] == 2

    def test_a_handled_follow_up_produces_no_new_task(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user, handled=True)

        assert (
            client.post("/tasks/from-follow-ups", headers=user_headers).json()["total"]
            == 0
        )

    def test_a_message_triage_did_not_flag_produces_nothing(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user, recommended=False)

        assert (
            client.post("/tasks/from-follow-ups", headers=user_headers).json()["total"]
            == 0
        )


class TestIsolation:
    def test_one_persons_follow_up_never_becomes_anothers_task(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user_b, subject="Their private thread")

        mine = client.post("/tasks/from-follow-ups", headers=user_headers).json()

        assert mine["total"] == 0

        theirs = client.post("/tasks/from-follow-ups", headers=user_b_headers).json()

        assert theirs["total"] == 1

    def test_the_same_broadcast_message_gives_each_person_their_own_task(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
    ):
        # `source_key` is unique *per user*, so a message sent to both of them
        # is two tasks rather than a collision.
        for user in (test_user, test_user_b):
            follow_up(db_session, user=user, message_id="broadcast")

        mine = client.post("/tasks/from-follow-ups", headers=user_headers).json()
        theirs = client.post("/tasks/from-follow-ups", headers=user_b_headers).json()

        assert mine["total"] == 1
        assert theirs["total"] == 1
        assert mine["items"][0]["id"] != theirs["items"][0]["id"]

    def test_switching_identity_switches_the_whole_chain(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        test_user_b: User,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user, subject="Mine")
        follow_up(db_session, user=test_user_b, subject="Theirs")

        client.post("/tasks/from-follow-ups", headers=user_headers)
        client.post("/tasks/from-follow-ups", headers=user_b_headers)

        for headers, mine, theirs in (
            (user_headers, "Mine", "Theirs"),
            (user_b_headers, "Theirs", "Mine"),
        ):
            tasks = client.get("/tasks", headers=headers).text
            report = client.get("/reports/weekly", headers=headers).text

            assert mine in tasks
            assert theirs not in tasks
            assert mine in report
            assert theirs not in report


class TestCompletion:
    def test_completing_the_task_moves_it_out_of_the_open_sections(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        follow_up(db_session, user=test_user, due_at=NOW - timedelta(days=9))
        task = client.post("/tasks/from-follow-ups", headers=user_headers).json()[
            "items"
        ][0]

        done = client.post(f"/tasks/{task['id']}/complete", headers=user_headers)

        assert done.status_code == 200

        body = client.get("/reports/weekly", headers=user_headers).json()

        assert body["overdue"] == []
        assert body["overdue_count"] == 0
        # An overdue task that has been completed is an achievement, not a
        # standing escalation.
        assert body["escalation_count"] == 0
        assert [item["title"] for item in body["completed"]] == [task["title"]]


class TestOneFollowUpAtATime:
    """The "Add to Tasks" button on a single triaged email.

    The bulk sweep answers "make tasks for everything", which is useful and a
    poor button: being told "4 tasks created" tells a person nothing about the
    one message they were looking at. This endpoint returns the task, so the
    UI can show it.
    """

    def test_adding_one_returns_the_task_itself(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        assessment = follow_up(db_session, user=test_user)

        response = client.post(
            f"/tasks/from-follow-up/{assessment.id}", headers=user_headers
        )

        assert response.status_code == 200

        task = response.json()
        assert task["title"] == "Follow up: Revised proposal numbers"
        assert task["source"] == "email_follow_up"
        assert task["signal"] == "todo"
        # The triage summary carries through as the task's description rather
        # than a second model call producing a second opinion.
        assert task["description"] == "They asked twice and have had no answer."

    def test_pressing_it_twice_returns_the_same_task(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        assessment = follow_up(db_session, user=test_user)

        first = client.post(
            f"/tasks/from-follow-up/{assessment.id}", headers=user_headers
        ).json()
        second = client.post(
            f"/tasks/from-follow-up/{assessment.id}", headers=user_headers
        ).json()

        assert first["id"] == second["id"]
        assert client.get("/tasks", headers=user_headers).json()["total"] == 1

    def test_it_agrees_with_the_bulk_sweep(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        # Both go through `sync_follow_up_task`, so neither can produce a task
        # the other would not, and running one after the other adds nothing.
        assessment = follow_up(db_session, user=test_user)

        single = client.post(
            f"/tasks/from-follow-up/{assessment.id}", headers=user_headers
        ).json()
        swept = client.post("/tasks/from-follow-ups", headers=user_headers).json()

        assert swept["total"] == 1
        assert swept["items"][0]["id"] == single["id"]

    def test_a_message_triage_found_no_action_in_is_refused_with_a_reason(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        # Not a failure: the message was read and judged not to need action.
        # Inventing a task from it would put work on somebody's list that
        # nothing asked for.
        assessment = follow_up(db_session, user=test_user, recommended=False)

        refused = client.post(
            f"/tasks/from-follow-up/{assessment.id}", headers=user_headers
        )

        assert refused.status_code == 422
        assert "did not identify an action" in refused.json()["detail"]

    def test_another_persons_triaged_message_is_a_404(
        self,
        client: TestClient,
        db_session: Session,
        test_user_b: User,
        user_headers: dict[str, str],
    ):
        theirs = follow_up(db_session, user=test_user_b)

        refused = client.post(
            f"/tasks/from-follow-up/{theirs.id}", headers=user_headers
        )

        assert refused.status_code == 404

    def test_an_id_naming_nothing_is_a_404(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        missing = "00000000-0000-0000-0000-000000000000"

        assert (
            client.post(
                f"/tasks/from-follow-up/{missing}", headers=user_headers
            ).status_code
            == 404
        )
