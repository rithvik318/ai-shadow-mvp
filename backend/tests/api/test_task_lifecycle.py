"""The task lifecycle over HTTP: todo → in progress → completed.

Written after Start returned 500 on every PostgreSQL deployment. The cause was
not in this layer at all — `task.status` was `VARCHAR(9)` and `in_progress` is
eleven characters, fixed in migration 0013 — but the absence of a test that
walked the lifecycle end to end is why nobody found out before a user did.
`tests/database/test_migration_fidelity.py` catches the schema half; this
catches the behaviour half.

The refusals matter as much as the happy path. A lifecycle in which every move
is allowed is not a lifecycle, and one that answers a forbidden move with a 500
tells a person their action broke the server when it was simply not available.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import User

NOW = datetime.now(UTC)


def make(client: TestClient, headers: dict[str, str], **fields) -> dict:
    body = {"title": "Submit the revised numbers", **fields}
    response = client.post("/tasks", json=body, headers=headers)

    assert response.status_code == 201, response.text

    return response.json()


def move(client: TestClient, headers: dict[str, str], task_id: str, status: str):
    return client.patch(f"/tasks/{task_id}", json={"status": status}, headers=headers)


class TestTheHappyPath:
    def test_a_task_can_be_started_and_then_completed(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        task = make(client, user_headers)

        assert task["status"] == "todo"

        started = move(client, user_headers, task["id"], "in_progress")

        assert started.status_code == 200
        assert started.json()["status"] == "in_progress"
        assert started.json()["display_status"] == "in_progress"
        assert started.json()["completed_at"] is None

        done = client.post(f"/tasks/{task['id']}/complete", headers=user_headers)

        assert done.status_code == 200
        assert done.json()["status"] == "completed"
        assert done.json()["completed_at"] is not None

    def test_starting_is_idempotent(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # Pressing the button twice is a person being unsure, not an error.
        task = make(client, user_headers)
        move(client, user_headers, task["id"], "in_progress")

        again = move(client, user_headers, task["id"], "in_progress")

        assert again.status_code == 200
        assert again.json()["status"] == "in_progress"

    def test_starting_an_overdue_task_keeps_it_overdue(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # Starting work does not make a missed deadline stop having been missed.
        task = make(
            client,
            user_headers,
            due_at=(NOW - timedelta(days=3)).isoformat(),
        )

        started = move(client, user_headers, task["id"], "in_progress").json()

        assert started["is_overdue"] is True
        assert started["urgency"] == "critical"

    def test_blocked_work_can_be_picked_up_again(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        task = make(client, user_headers)
        move(client, user_headers, task["id"], "blocked")

        resumed = move(client, user_headers, task["id"], "in_progress")

        assert resumed.status_code == 200
        assert resumed.json()["status"] == "in_progress"


class TestRefusals:
    def test_a_completed_task_cannot_be_started(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # The rule the lifecycle exists for. Allowing this would let "done"
        # drift into "in progress" as a side effect of a misclick, and would
        # clear the completion time on the way.
        task = make(client, user_headers)
        client.post(f"/tasks/{task['id']}/complete", headers=user_headers)

        refused = move(client, user_headers, task["id"], "in_progress")

        assert refused.status_code == 409
        assert refused.json()["error"] == "TaskTransitionError"
        assert "completed" in refused.json()["detail"]

    def test_a_refused_move_leaves_the_task_exactly_as_it_was(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        task = make(client, user_headers)
        completed = client.post(
            f"/tasks/{task['id']}/complete", headers=user_headers
        ).json()

        move(client, user_headers, task["id"], "in_progress")

        after = client.get(f"/tasks/{task['id']}", headers=user_headers).json()

        assert after["status"] == "completed"
        assert after["completed_at"] == completed["completed_at"]

    def test_a_completed_task_can_still_be_deliberately_reopened(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        # The one move out of completed, and it is a correction rather than a
        # lifecycle step: somebody says the work came back.
        task = make(client, user_headers)
        client.post(f"/tasks/{task['id']}/complete", headers=user_headers)

        reopened = move(client, user_headers, task["id"], "todo")

        assert reopened.status_code == 200
        assert reopened.json()["status"] == "todo"
        # A reopened task that still claims a completion time is a lie.
        assert reopened.json()["completed_at"] is None

    def test_a_completed_task_cannot_be_blocked(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        task = make(client, user_headers)
        client.post(f"/tasks/{task['id']}/complete", headers=user_headers)

        assert move(client, user_headers, task["id"], "blocked").status_code == 409

    @pytest.mark.parametrize("status", ["done", "started", "IN_PROGRESS", ""])
    def test_a_status_this_system_does_not_have_is_a_422_not_a_500(
        self, client: TestClient, user_headers: dict[str, str], status: str
    ):
        task = make(client, user_headers)

        assert move(client, user_headers, task["id"], status).status_code == 422

    def test_starting_somebody_elses_task_is_a_404(
        self,
        client: TestClient,
        test_user_b: User,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
    ):
        # 404 rather than 403: confirming that another person's task exists is
        # itself a disclosure.
        theirs = make(client, user_b_headers)

        refused = move(client, user_headers, theirs["id"], "in_progress")

        assert refused.status_code == 404


class TestTheReportFollows:
    def test_starting_moves_nothing_but_completing_does(
        self,
        client: TestClient,
        db_session: Session,
        test_user: User,
        user_headers: dict[str, str],
    ):
        task = make(
            client,
            user_headers,
            due_at=(NOW - timedelta(days=9)).isoformat(),
            priority="high",
        )

        move(client, user_headers, task["id"], "in_progress")
        started = client.get("/reports/weekly", headers=user_headers).json()

        # Work in progress is still outstanding work: it stays overdue, stays
        # escalated, and is not an achievement yet.
        assert [item["title"] for item in started["overdue"]] == [task["title"]]
        assert started["escalation_count"] == 1
        assert started["completed"] == []

        client.post(f"/tasks/{task['id']}/complete", headers=user_headers)
        finished = client.get("/reports/weekly", headers=user_headers).json()

        assert finished["overdue"] == []
        assert finished["overdue_count"] == 0
        assert finished["escalation_count"] == 0
        assert [item["title"] for item in finished["completed"]] == [task["title"]]
        assert finished["completed_count"] == 1

    def test_an_in_progress_task_is_listed_among_open_work(
        self, client: TestClient, user_headers: dict[str, str]
    ):
        task = make(client, user_headers, due_at=(NOW + timedelta(days=2)).isoformat())
        move(client, user_headers, task["id"], "in_progress")

        body = client.get("/reports/weekly", headers=user_headers).json()

        assert [item["title"] for item in body["upcoming"]] == [task["title"]]
        assert [item["title"] for item in body["deadlines"]] == [task["title"]]
