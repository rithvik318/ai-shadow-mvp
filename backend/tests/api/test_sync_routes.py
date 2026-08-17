"""The synchronisation endpoints.

Graph is replaced at the service boundary rather than at the HTTP one: these
are about what the API reports and what it refuses, and the service's own
behaviour is covered where it lives.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import GraphAuthError, SyncNotConfiguredError
from app.models.sync import SyncStatus
from app.services.features.sync.onedrive_sync_service import (
    FileOutcome,
    SyncResult,
    SyncSummary,
)
from tests.support.graph import DRIVE_ID, sources_json

ENDPOINT = "/sync/onedrive"
STATUS_ENDPOINT = "/sync/onedrive/status"


def _summary(**overrides) -> SyncSummary:
    summary = SyncSummary(
        source_key="capabilities",
        label="Capabilities",
        mode="incremental",
        status=SyncStatus.SUCCEEDED,
        discovered=3,
        indexed=1,
        replaced=1,
        unchanged=1,
        duration_seconds=1.25,
        delta_advanced=True,
    )

    for name, value in overrides.items():
        setattr(summary, name, value)

    return summary


@pytest.fixture
def stub_sync(monkeypatch: pytest.MonkeyPatch):
    """Replace the sync service, returning the recorded call arguments."""

    from app.services.features.sync import onedrive_sync_service

    calls: list[dict] = []

    def install(result):
        def sync_all(_db, **kwargs):
            calls.append(kwargs)

            if isinstance(result, Exception):
                raise result

            return result

        monkeypatch.setattr(onedrive_sync_service, "sync_all", sync_all)
        return calls

    return install


# --- running a sync ------------------------------------------------------


def test_a_sync_reports_per_source_and_total_counts(client: TestClient, stub_sync):
    stub_sync([_summary()])

    response = client.post(ENDPOINT, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["total_discovered"] == 3
    assert body["total_indexed"] == 1
    assert body["total_replaced"] == 1
    assert body["total_unchanged"] == 1
    assert body["sources"][0]["source_key"] == "capabilities"
    assert body["sources"][0]["mode"] == "incremental"


def test_totals_add_up_across_sources(client: TestClient, stub_sync):
    stub_sync([_summary(), _summary(source_key="freddie-mac", indexed=4)])

    body = client.post(ENDPOINT, json={}).json()

    assert body["total_indexed"] == 5
    assert len(body["sources"]) == 2


def test_a_sync_defaults_to_incremental_over_every_source(
    client: TestClient, stub_sync
):
    calls = stub_sync([_summary()])

    client.post(ENDPOINT, json={})

    assert calls == [{"source_key": None, "full": False}]


def test_a_body_is_optional(client: TestClient, stub_sync):
    calls = stub_sync([_summary()])

    assert client.post(ENDPOINT).status_code == 200
    assert calls == [{"source_key": None, "full": False}]


def test_one_source_can_be_named(client: TestClient, stub_sync):
    calls = stub_sync([_summary()])

    client.post(ENDPOINT, json={"source": "capabilities"})

    assert calls == [{"source_key": "capabilities", "full": False}]


def test_a_full_resync_can_be_requested(client: TestClient, stub_sync):
    calls = stub_sync([_summary()])

    client.post(ENDPOINT, json={"full": True})

    assert calls == [{"source_key": None, "full": True}]


def test_per_file_results_are_reported(client: TestClient, stub_sync):
    """A total of "3 failed" is not actionable. Which three is."""

    summary = _summary()
    summary.outcomes = [
        FileOutcome(
            name="broken.pdf",
            source_uri=f"onedrive:{DRIVE_ID}:i1",
            result=SyncResult.FAILED,
            reason="PDF could not be read.",
        )
    ]
    stub_sync([summary])

    files = client.post(ENDPOINT, json={}).json()["sources"][0]["files"]

    assert files[0]["name"] == "broken.pdf"
    assert files[0]["result"] == "failed"
    assert files[0]["reason"]


def test_a_partial_run_reports_that_the_token_did_not_move(
    client: TestClient, stub_sync
):
    """The caller needs to know the next run will re-examine this window,
    rather than assuming everything before it is done."""

    stub_sync([_summary(status=SyncStatus.PARTIAL, delta_advanced=False, failed=1)])

    source = client.post(ENDPOINT, json={}).json()["sources"][0]

    assert source["status"] == "partial"
    assert source["delta_advanced"] is False


# --- refusals ------------------------------------------------------------


def test_an_unconfigured_deployment_gets_a_409(client: TestClient, stub_sync):
    """Well-formed request, server not in a state to satisfy it."""

    stub_sync(SyncNotConfiguredError("No OneDrive sources are configured."))

    response = client.post(ENDPOINT, json={})

    assert response.status_code == 409
    assert response.json()["error"] == "SyncNotConfiguredError"


def test_graph_refusing_the_credentials_is_a_502(client: TestClient, stub_sync):
    """The far end, not this one — worth distinguishing in any dashboard built
    on status codes."""

    stub_sync(GraphAuthError("Graph denied the request (HTTP 403)."))

    response = client.post(ENDPOINT, json={})

    assert response.status_code == 502
    assert response.json()["error"] == "GraphAuthError"


# --- status --------------------------------------------------------------


def test_status_is_empty_before_anything_has_run(client: TestClient) -> None:
    body = client.get(STATUS_ENDPOINT).json()

    assert body["sources"] == []
    assert body["configured"] is False
    assert body["scheduled"] is False


def test_status_reports_configured_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "ONEDRIVE_SOURCES",
        sources_json({"key": "capabilities", "path": "C", "drive_id": DRIVE_ID}),
    )

    assert client.get(STATUS_ENDPOINT).json()["configured"] is True


def test_malformed_configuration_reads_as_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status endpoint's job is to report, not to fail. A broken
    ONEDRIVE_SOURCES is exactly when somebody needs to load this page."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_SOURCES", "{not json")

    response = client.get(STATUS_ENDPOINT)

    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_status_reports_the_schedule(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_SYNC_ENABLED", True)
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_SYNC_INTERVAL_SECONDS", 900)

    body = client.get(STATUS_ENDPOINT).json()

    assert body["scheduled"] is True
    assert body["interval_seconds"] == 900


def test_status_reports_state_after_a_run(client: TestClient, db_session) -> None:
    from app.models.sync import OneDriveSyncState

    db_session.add(
        OneDriveSyncState(
            source_key="capabilities",
            label="Capabilities",
            drive_id=DRIVE_ID,
            item_id="folder-1",
            delta_link="https://graph.example/delta?token=secret-token",
            status=SyncStatus.SUCCEEDED,
            last_indexed=7,
        )
    )
    db_session.commit()

    source = client.get(STATUS_ENDPOINT).json()["sources"][0]

    assert source["source_key"] == "capabilities"
    assert source["status"] == "succeeded"
    assert source["last_indexed"] == 7


def test_the_delta_token_is_never_returned(client: TestClient, db_session) -> None:
    """It is a bearer credential for the window it describes, and a status
    page is for people reading it, not for resuming a sync."""

    from app.models.sync import OneDriveSyncState

    db_session.add(
        OneDriveSyncState(
            source_key="capabilities",
            delta_link="https://graph.example/delta?token=secret-token",
            status=SyncStatus.SUCCEEDED,
        )
    )
    db_session.commit()

    response = client.get(STATUS_ENDPOINT)

    assert "secret-token" not in response.text
    assert response.json()["sources"][0]["has_delta_token"] is True


def test_no_credential_is_ever_in_a_sync_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_SECRET", "s3cr3t")

    body = client.get(STATUS_ENDPOINT).text

    assert "s3cr3t" not in body
