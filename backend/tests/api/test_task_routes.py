"""Task and weekly-report endpoints, and the boundary between two people.

The API is where a leak would actually happen, so isolation is asserted here as
well as at the service — a service can be perfectly scoped and still be reached
through a route that resolves its owner from the wrong place.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "TASK_WARNING_DAYS", 3)
    monkeypatch.setattr(settings_module.settings, "TASK_ESCALATION_DAYS", 7)
    monkeypatch.setattr(settings_module.settings, "ESCALATION_CONTACT_ADDRESS", None)
    monkeypatch.setattr(settings_module.settings, "ESCALATION_CONTACT_NAME", None)


def _in(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


# --- identity ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/tasks"),
        ("post", "/tasks"),
        ("get", "/reports/weekly"),
    ],
)
def test_task_endpoints_refuse_an_anonymous_caller(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method.upper(), path, json={"title": "x"})

    assert response.status_code == 401


# --- CRUD ----------------------------------------------------------------


def test_a_task_round_trips_through_the_api(
    client: TestClient, user_headers: dict
) -> None:
    created = client.post(
        "/tasks",
        json={"title": "Send the pro forma", "priority": "high", "due_at": _in(1)},
        headers=user_headers,
    )

    assert created.status_code == 201
    body = created.json()

    assert body["title"] == "Send the pro forma"
    assert body["status"] == "todo"
    # The server decided the colour semantics, not the client.
    assert body["urgency"] == "warning"

    listed = client.get("/tasks", headers=user_headers).json()

    assert listed["total"] == 1


def test_an_empty_title_is_rejected(client: TestClient, user_headers: dict) -> None:
    response = client.post("/tasks", json={"title": "   "}, headers=user_headers)

    assert response.status_code == 422


def test_completing_a_task_persists_and_turns_it_green(
    client: TestClient, user_headers: dict
) -> None:
    """The checkbox interaction, end to end. Completion must not be local-only."""

    task_id = client.post(
        "/tasks", json={"title": "Thing", "due_at": _in(-2)}, headers=user_headers
    ).json()["id"]

    before = client.get(f"/tasks/{task_id}", headers=user_headers).json()

    assert before["display_status"] == "overdue"
    assert before["urgency"] == "critical"

    completed = client.post(f"/tasks/{task_id}/complete", headers=user_headers).json()

    assert completed["status"] == "completed"
    assert completed["urgency"] == "normal"
    assert completed["completed_at"] is not None

    # Re-read from the server rather than trusting the response body.
    after = client.get(f"/tasks/{task_id}", headers=user_headers).json()

    assert after["status"] == "completed"


def test_an_overdue_task_stays_red_until_it_is_completed(
    client: TestClient, user_headers: dict
) -> None:
    task_id = client.post(
        "/tasks", json={"title": "Late", "due_at": _in(-9)}, headers=user_headers
    ).json()["id"]

    assert (
        client.get(f"/tasks/{task_id}", headers=user_headers).json()["urgency"]
        == "critical"
    )

    client.patch(
        f"/tasks/{task_id}", json={"description": "Chased once"}, headers=user_headers
    )

    # Editing something unrelated does not resolve it.
    assert (
        client.get(f"/tasks/{task_id}", headers=user_headers).json()["urgency"]
        == "critical"
    )


def test_a_patch_only_changes_what_it_sends(
    client: TestClient, user_headers: dict
) -> None:
    """Omitted fields must not be cleared to null."""

    task_id = client.post(
        "/tasks",
        json={"title": "Thing", "description": "Keep me", "due_at": _in(5)},
        headers=user_headers,
    ).json()["id"]

    client.patch(f"/tasks/{task_id}", json={"title": "Renamed"}, headers=user_headers)

    body = client.get(f"/tasks/{task_id}", headers=user_headers).json()

    assert body["title"] == "Renamed"
    assert body["description"] == "Keep me"
    assert body["due_at"] is not None


def test_deleting_a_task_removes_it(client: TestClient, user_headers: dict) -> None:
    task_id = client.post(
        "/tasks", json={"title": "Temp"}, headers=user_headers
    ).json()["id"]

    assert client.delete(f"/tasks/{task_id}", headers=user_headers).status_code == 204
    assert client.get(f"/tasks/{task_id}", headers=user_headers).status_code == 404


# --- isolation -----------------------------------------------------------


def test_one_user_cannot_see_anothers_tasks(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    client.post("/tasks", json={"title": "Mine"}, headers=user_headers)

    assert client.get("/tasks", headers=user_b_headers).json()["total"] == 0


def test_one_user_cannot_read_anothers_task_by_id(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    task_id = client.post(
        "/tasks", json={"title": "Mine"}, headers=user_headers
    ).json()["id"]

    assert client.get(f"/tasks/{task_id}", headers=user_b_headers).status_code == 404


def test_one_user_cannot_complete_anothers_task(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    task_id = client.post(
        "/tasks", json={"title": "Mine"}, headers=user_headers
    ).json()["id"]

    assert (
        client.post(f"/tasks/{task_id}/complete", headers=user_b_headers).status_code
        == 404
    )
    assert (
        client.get(f"/tasks/{task_id}", headers=user_headers).json()["status"] == "todo"
    )


def test_the_weekly_report_is_one_persons_work_only(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    client.post(
        "/tasks", json={"title": "Mine", "due_at": _in(1)}, headers=user_headers
    )
    client.post(
        "/tasks", json={"title": "Theirs", "due_at": _in(1)}, headers=user_b_headers
    )

    mine = client.get("/reports/weekly", headers=user_headers).json()
    theirs = client.get("/reports/weekly", headers=user_b_headers).json()

    assert [item["title"] for item in mine["upcoming"]] == ["Mine"]
    assert [item["title"] for item in theirs["upcoming"]] == ["Theirs"]


# --- the weekly report ---------------------------------------------------


def test_the_report_carries_counts_and_sections(
    client: TestClient, user_headers: dict
) -> None:
    client.post(
        "/tasks", json={"title": "Soon", "due_at": _in(2)}, headers=user_headers
    )
    client.post(
        "/tasks", json={"title": "Late", "due_at": _in(-2)}, headers=user_headers
    )

    body = client.get("/reports/weekly", headers=user_headers).json()

    assert body["due_soon_count"] == 1
    assert body["overdue_count"] == 1
    assert [item["title"] for item in body["overdue"]] == ["Late"]


def test_the_report_states_that_the_calendar_is_not_connected(
    client: TestClient, user_headers: dict
) -> None:
    """No invented meetings, and the absence is explicit rather than an empty list."""

    body = client.get("/reports/weekly", headers=user_headers).json()

    assert body["calendar_connected"] is False
    assert body["events"] == []
    assert "no provider connected" in body["calendar_detail"].lower()


def test_an_escalation_says_no_contact_is_configured(
    client: TestClient, user_headers: dict
) -> None:
    client.post(
        "/tasks",
        json={"title": "Late and important", "priority": "urgent", "due_at": _in(-2)},
        headers=user_headers,
    )

    body = client.get("/reports/weekly", headers=user_headers).json()

    assert body["escalation_count"] == 1
    escalation = body["escalations"][0]

    assert escalation["escalation_contact_address"] is None
    assert (
        escalation["escalation_contact_display"] == "Escalation target not identified"
    )
    assert escalation["reason"]
    assert escalation["recommended_action"]


# --- events --------------------------------------------------------------


def _event(client: TestClient, headers: dict, **overrides):
    body: dict = {"title": "Design review", "starts_at": _in(1)}
    body.update(overrides)

    return client.post("/events", json=body, headers=headers)


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/events"), ("post", "/events")],
)
def test_event_endpoints_refuse_an_anonymous_caller(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(
        method.upper(), path, json={"title": "x", "starts_at": _in(1)}
    )

    assert response.status_code == 401


def test_a_new_event_is_scheduled_and_not_attended(
    client: TestClient, user_headers: dict
) -> None:
    """Even one created in the past. Nothing about attendance is assumed."""

    body = _event(client, user_headers, starts_at=_in(-3)).json()

    assert body["status"] == "scheduled"
    # It is over and nobody has said what happened, so it *reads* as unknown.
    assert body["display_status"] == "unknown"
    assert body["needs_answer"] is True
    assert body["attendance_evidence"] == "none"


def test_a_future_event_is_not_asked_about(
    client: TestClient, user_headers: dict
) -> None:
    body = _event(client, user_headers).json()

    assert body["display_status"] == "scheduled"
    assert body["needs_answer"] is False


def test_a_person_can_record_attendance(client: TestClient, user_headers: dict) -> None:
    event_id = _event(client, user_headers, starts_at=_in(-1)).json()["id"]

    marked = client.post(
        f"/events/{event_id}/attendance",
        json={"status": "attended"},
        headers=user_headers,
    ).json()

    assert marked["status"] == "attended"
    assert marked["display_status"] == "attended"
    assert marked["needs_answer"] is False
    assert marked["attendance_evidence"] == "user"


def test_an_attendance_answer_can_be_withdrawn(
    client: TestClient, user_headers: dict
) -> None:
    event_id = _event(client, user_headers, starts_at=_in(-1)).json()["id"]
    client.post(
        f"/events/{event_id}/attendance",
        json={"status": "attended"},
        headers=user_headers,
    )

    withdrawn = client.post(
        f"/events/{event_id}/attendance",
        json={"status": "unknown"},
        headers=user_headers,
    ).json()

    assert withdrawn["needs_answer"] is True
    assert withdrawn["attendance_evidence"] == "none"


def test_editing_an_event_cannot_set_attendance(
    client: TestClient, user_headers: dict
) -> None:
    """The PATCH schema has no `status`, so an attempt is ignored rather than
    honoured — recording attendance has its own endpoint on purpose."""

    event_id = _event(client, user_headers, starts_at=_in(-1)).json()["id"]

    patched = client.patch(
        f"/events/{event_id}",
        json={"title": "Renamed", "status": "attended"},
        headers=user_headers,
    ).json()

    assert patched["title"] == "Renamed"
    assert patched["status"] == "scheduled"


def test_an_organiser_display_string_is_split(
    client: TestClient, user_headers: dict
) -> None:
    body = _event(
        client,
        user_headers,
        organiser="Robert Keenan <Robert.Keenan@sunradia.com>",
    ).json()

    assert body["organiser_name"] == "Robert Keenan"
    assert body["organiser_address"] == "Robert.Keenan@sunradia.com"


def test_one_user_cannot_see_anothers_events(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    _event(client, user_headers, title="Mine")

    assert client.get("/events", headers=user_b_headers).json()["total"] == 0


def test_one_user_cannot_answer_for_anothers_event(
    client: TestClient, user_headers: dict, user_b_headers: dict
) -> None:
    event_id = _event(client, user_headers, starts_at=_in(-1)).json()["id"]

    assert (
        client.post(
            f"/events/{event_id}/attendance",
            json={"status": "attended"},
            headers=user_b_headers,
        ).status_code
        == 404
    )
    assert (
        client.get(f"/events/{event_id}", headers=user_headers).json()["status"]
        == "scheduled"
    )


def test_an_event_that_ends_before_it_starts_is_refused(
    client: TestClient, user_headers: dict
) -> None:
    assert _event(client, user_headers, ends_at=_in(-5)).status_code == 422


# --- the report's new sections -------------------------------------------


def test_the_report_carries_events_and_asks_about_the_unanswered(
    client: TestClient, user_headers: dict
) -> None:
    _event(client, user_headers, title="Past", starts_at=_in(-2))
    _event(client, user_headers, title="Future", starts_at=_in(2))

    body = client.get("/reports/weekly", headers=user_headers).json()

    assert {item["title"] for item in body["events"]} == {"Past", "Future"}
    assert [item["title"] for item in body["events_needing_answer"]] == ["Past"]
    assert body["summary"]["events_needing_answer_count"] == 1


def test_the_report_summary_agrees_with_its_sections(
    client: TestClient, user_headers: dict
) -> None:
    client.post(
        "/tasks", json={"title": "Late", "due_at": _in(-2)}, headers=user_headers
    )

    body = client.get("/reports/weekly", headers=user_headers).json()

    assert body["summary"]["overdue_count"] == body["overdue_count"]
    assert body["summary"]["overdue_count"] == len(body["overdue"])


def test_a_task_can_be_started_before_it_is_finished(
    client: TestClient, user_headers: dict
) -> None:
    """`in_progress` is the state the old vocabulary could not express."""

    task_id = client.post(
        "/tasks", json={"title": "Thing"}, headers=user_headers
    ).json()["id"]

    body = client.patch(
        f"/tasks/{task_id}", json={"status": "in_progress"}, headers=user_headers
    ).json()

    assert body["status"] == "in_progress"
    assert body["display_status"] == "in_progress"


def test_an_escalated_task_reads_as_escalation_required(
    client: TestClient, user_headers: dict
) -> None:
    """Escalation outranks overdue in `display_status`: an overdue task tells
    its owner to get on with it, an escalated one tells them it is no longer
    theirs alone to finish."""

    task_id = client.post(
        "/tasks",
        json={"title": "Approval", "priority": "high", "due_at": _in(-4)},
        headers=user_headers,
    ).json()["id"]

    body = client.get(f"/tasks/{task_id}", headers=user_headers).json()

    assert body["is_overdue"] is True
    assert body["display_status"] == "escalation_required"
    assert body["escalation_required"] is True
    assert body["escalation_reason"]
    assert body["escalation_action"]


def test_an_ordinary_overdue_task_still_reads_as_overdue(
    client: TestClient, user_headers: dict
) -> None:
    task_id = client.post(
        "/tasks", json={"title": "Late", "due_at": _in(-1)}, headers=user_headers
    ).json()["id"]

    body = client.get(f"/tasks/{task_id}", headers=user_headers).json()

    assert body["display_status"] == "overdue"
    assert body["escalation_required"] is False


def test_an_escalation_names_a_configured_target(
    client: TestClient, user_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "ESCALATION_CONTACT_ADDRESS", "sudha@sunradia.com"
    )
    monkeypatch.setattr(settings_module.settings, "ESCALATION_CONTACT_NAME", "Sudha P")

    client.post(
        "/tasks",
        json={"title": "Approval", "priority": "high", "due_at": _in(-4)},
        headers=user_headers,
    )

    escalation = client.get("/reports/weekly", headers=user_headers).json()[
        "escalations"
    ][0]

    assert escalation["escalation_contact_address"] == "sudha@sunradia.com"
    assert escalation["target_source"] == "configured"
    assert escalation["recommended_action"] == "Prepare an escalation email for review."
