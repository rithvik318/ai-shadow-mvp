"""A report is generated once. These tests are what makes that true.

The endpoint used to rebuild the document on every request: a mailbox read,
classification, threading, a dozen model calls and a composition — for a week
that had closed and could not have a different answer. Sep 7–13 took over 200
seconds, twice, for the same document.

So every test here counts two things, because they are the two that cost
minutes: **provider calls** and **model calls**. A wording assertion would not
have caught the original defect and will not catch its return.

`now` is never frozen. The windows are built relative to the real clock — last
week is closed, this week is open — and the age of a stored report is changed
by writing to its row, which is what a clock passing would have done anyway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import report_routes
from app.models.activity_report import ActivityReport, ActivityReportStatus
from app.services.features.reports import activity_store
from app.services.features.reports import analysis as analysis_stage
from tests.support.email import RecordingProvider
from tests.support.email import message as provider_message

MAILBOX = "owner@sunradia.com"
NOW = datetime.now(UTC)

#: A window that has closed: the seven days ending yesterday.
CLOSED_END = (NOW - timedelta(days=1)).date()
CLOSED_START = CLOSED_END - timedelta(days=6)

#: A window that has not: today, and the six days before it.
OPEN_START = (NOW - timedelta(days=6)).date()


def closed_params() -> dict[str, str]:
    return {"start": CLOSED_START.isoformat(), "end": CLOSED_END.isoformat()}


def open_params() -> dict[str, str]:
    return {"start": OPEN_START.isoformat()}


def a_message(index: int, *, days_ago: int, folder: str | None = None):
    return provider_message(
        message_id=f"m-{index}",
        thread_id=f"t-{index}",
        sender=MAILBOX if folder else f"client{index}@acme.example",
        subject=f"Site {index}: revised delivery schedule",
        body="Could you confirm the milestone dates before the site visit?",
        received_at=NOW - timedelta(days=days_ago, hours=1),
        folder=folder,
    )


class Counter:
    """Stands in for the analysis engine and counts what it was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, prompt_name: str, response_model: type, **variables: object):
        self.calls.append(prompt_name)

        return response_model()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Counter:
    counter = Counter()
    monkeypatch.setattr(analysis_stage.analysis_engine, "run", counter)
    # With no credentials the pipeline never calls the engine at all, and a
    # test that counts model calls has to be able to see them.
    monkeypatch.setattr(report_routes, "llm_configured", lambda: True)

    return counter


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> RecordingProvider:
    """A connected mailbox with two days of correspondence in each window."""

    stub = RecordingProvider(
        messages=[
            a_message(1, days_ago=3),
            a_message(2, days_ago=3, folder="sentitems"),
            a_message(3, days_ago=2),
        ]
    )

    monkeypatch.setattr(
        report_routes.mailbox_config_service,
        "resolve_address",
        lambda db, *, user_id: MAILBOX,
    )
    monkeypatch.setattr(
        report_routes.mailbox_config_service,
        "provider_for",
        lambda db, *, user_id: stub,
    )

    return stub


def rows(db: Session, user_id: uuid.UUID) -> list[ActivityReport]:
    return activity_store.history(db, user_id=user_id)


class TestAClosedPeriod:
    def test_the_first_request_generates_and_stores_it(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        response = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert response.status_code == 200, response.text

        body = response.json()

        assert body["source"] == "generated"
        assert body["report_version"] == activity_store.REPORT_VERSION
        assert body["messages_analysed"] == 3
        assert model.calls, "the first request should do the expensive work"

        stored = rows(db_session, test_user.id)

        assert len(stored) == 1
        assert stored[0].status is ActivityReportStatus.COMPLETE
        assert stored[0].is_provisional is False
        assert stored[0].content["markdown"]
        assert stored[0].evidence_fingerprint

    def test_the_second_request_reads_storage_and_calls_nothing(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """The whole point. No mailbox, no model, no minutes."""

        first = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert first.status_code == 200
        provider.listed.clear()
        model.calls.clear()

        second = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert second.status_code == 200
        assert second.json()["source"] == "stored"
        assert provider.listed == [], "a closed period must not re-read the mailbox"
        assert model.calls == [], "a closed period must not call the model"

    def test_the_document_is_identical_between_the_two(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """A reader must not be able to tell which request they made.

        Only the provenance differs: the report is the same document, with the
        generation timestamp of when it was actually made.
        """

        first = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        ).json()
        second = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        ).json()

        assert second["markdown"] == first["markdown"]
        assert second["sections"] == first["sections"]
        assert second["generated_at"] == first["generated_at"]
        assert second["source"] != first["source"]

    def test_a_repeated_request_survives_a_new_session(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """Browser refresh, backend restart, next week: same storage.

        The database is the cache, so the reuse does not depend on this process
        remembering anything. Four requests, one generation.
        """

        for _ in range(4):
            response = client.get(
                "/reports/activity", params=closed_params(), headers=user_headers
            )

            assert response.status_code == 200

        assert len(provider.listed) == 2, "one fetch: inbox and sent folder"
        assert model.calls.count("report_thread_analysis") == 1


class TestAnOpenPeriod:
    def test_a_fresh_report_is_served_without_touching_the_mailbox(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        client.get("/reports/activity", params=open_params(), headers=user_headers)
        provider.listed.clear()
        model.calls.clear()

        again = client.get(
            "/reports/activity", params=open_params(), headers=user_headers
        )

        assert again.json()["source"] == "stored"
        assert again.json()["is_provisional"] is True
        assert provider.listed == []
        assert model.calls == []

    def test_a_stale_report_is_revalidated_rather_than_rebuilt(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """Stale by the clock is not the same as out of date.

        Past the freshness window the mailbox is re-read — seconds — and if
        nothing has arrived the stored document still describes it exactly.
        Rebuilding it would spend a dozen model calls to produce the same
        words.
        """

        client.get("/reports/activity", params=open_params(), headers=user_headers)
        _age(db_session, test_user.id, by=timedelta(hours=4))
        provider.listed.clear()
        model.calls.clear()

        again = client.get(
            "/reports/activity", params=open_params(), headers=user_headers
        )

        assert again.status_code == 200
        assert again.json()["source"] == "revalidated"
        assert provider.listed, "the mailbox is re-read to answer the question"
        assert model.calls == [], "unchanged correspondence means the same report"

    def test_new_correspondence_does_rebuild_it(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        client.get("/reports/activity", params=open_params(), headers=user_headers)
        _age(db_session, test_user.id, by=timedelta(hours=4))
        provider._messages.append(a_message(9, days_ago=0))
        model.calls.clear()

        again = client.get(
            "/reports/activity", params=open_params(), headers=user_headers
        )

        assert again.json()["source"] == "generated"
        assert again.json()["messages_analysed"] == 4
        assert model.calls, "a period that genuinely changed is re-analysed"

    def test_the_response_says_how_current_the_evidence_is(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        body = client.get(
            "/reports/activity", params=open_params(), headers=user_headers
        ).json()

        assert body["data_through"], "a reader is entitled to know what it covers"
        assert body["checked_at"]
        assert body["generated_at"]


class TestRegenerating:
    def test_a_post_rebuilds_exactly_once(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """Regenerate is deliberate, and it is one rebuild.

        Not one per render, not one per tab visit: the GET that follows it
        reads the row the POST wrote.
        """

        client.get("/reports/activity", params=closed_params(), headers=user_headers)
        model.calls.clear()
        provider.listed.clear()

        rebuilt = client.post(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json()["source"] == "generated"
        assert model.calls, "a deliberate rebuild does the work"

        model.calls.clear()
        provider.listed.clear()

        after = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert after.json()["source"] == "stored"
        assert model.calls == []
        assert provider.listed == []

    def test_regenerating_replaces_the_stored_row_rather_than_adding_one(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        client.get("/reports/activity", params=closed_params(), headers=user_headers)
        client.post("/reports/activity", params=closed_params(), headers=user_headers)

        assert len(rows(db_session, test_user.id)) == 1


class TestTwoRequestsAtOnce:
    def test_the_second_is_told_it_is_being_generated(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """A claim exists before the work does.

        Simulated by leaving a fresh `generating` row in place, which is
        exactly what another request in another process would have left. The
        second request must not start a second pipeline.
        """

        db_session.add(
            ActivityReport(
                user_id=test_user.id,
                period_start=datetime.combine(
                    CLOSED_START, datetime.min.time(), tzinfo=UTC
                ),
                period_end=datetime.combine(
                    CLOSED_END + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                ),
                report_version=activity_store.REPORT_VERSION,
                status=ActivityReportStatus.GENERATING,
                started_at=datetime.now(UTC),
                content={},
            )
        )
        db_session.commit()

        response = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert response.status_code == 202
        assert response.json()["state"] == "generating"
        assert response.json()["elapsed_seconds"] >= 0
        assert model.calls == [], "the second request must not generate"
        assert provider.listed == []

    def test_an_abandoned_claim_is_taken_over(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """A process that died holding the claim must not freeze the window.

        Otherwise one crash makes a period permanently ungeneratable, which is
        a worse failure than the duplicate generation the lock prevents.
        """

        db_session.add(
            ActivityReport(
                user_id=test_user.id,
                period_start=datetime.combine(
                    CLOSED_START, datetime.min.time(), tzinfo=UTC
                ),
                period_end=datetime.combine(
                    CLOSED_END + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                ),
                report_version=activity_store.REPORT_VERSION,
                status=ActivityReportStatus.GENERATING,
                started_at=datetime.now(UTC) - timedelta(hours=6),
                content={},
            )
        )
        db_session.commit()

        response = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert response.status_code == 200
        assert response.json()["source"] == "generated"


class TestTheVersion:
    def test_a_new_version_does_not_read_an_old_row(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Raising the version retires every stored report, deliberately.

        A prompt or composition change means the stored document is no longer
        the document this code produces, and serving it would mix two versions
        of the product in one screen.
        """

        original = activity_store.REPORT_VERSION

        client.get("/reports/activity", params=closed_params(), headers=user_headers)
        model.calls.clear()

        monkeypatch.setattr(activity_store, "REPORT_VERSION", "99-test")

        again = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert again.json()["source"] == "generated"
        assert again.json()["report_version"] == "99-test"
        assert model.calls, "a new version rebuilds rather than reusing"

        stored = rows(db_session, test_user.id)

        assert {row.report_version for row in stored} == {original, "99-test"}
        assert len(stored) == 2, "the old row is retired, not destroyed"


class TestAFailedGeneration:
    def test_it_is_not_stored_as_a_report(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failure must never leave a row that reads as a finished report.

        The failure is recorded — with its reason — and the window is left free
        for the next attempt.
        """

        def explode(*args, **kwargs):
            raise RuntimeError("the mailbox fell over")

        monkeypatch.setattr(report_routes.activity_service, "generate_content", explode)

        with pytest.raises(RuntimeError):
            client.get(
                "/reports/activity", params=closed_params(), headers=user_headers
            )

        stored = rows(db_session, test_user.id)

        assert len(stored) == 1
        assert stored[0].status is ActivityReportStatus.FAILED
        assert stored[0].content == {}
        assert "RuntimeError" in (stored[0].detail or "")

    def test_the_next_request_tries_again(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        db_session: Session,
        test_user,
        provider: RecordingProvider,
        model: Counter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        failures = {"count": 0}
        real = report_routes.activity_service.generate_content

        def once(*args, **kwargs):
            if failures["count"] == 0:
                failures["count"] += 1
                raise RuntimeError("the mailbox fell over")

            return real(*args, **kwargs)

        monkeypatch.setattr(report_routes.activity_service, "generate_content", once)

        with pytest.raises(RuntimeError):
            client.get(
                "/reports/activity", params=closed_params(), headers=user_headers
            )

        recovered = client.get(
            "/reports/activity", params=closed_params(), headers=user_headers
        )

        assert recovered.status_code == 200
        assert recovered.json()["source"] == "generated"
        assert rows(db_session, test_user.id)[0].status is ActivityReportStatus.COMPLETE


class TestIsolation:
    def test_one_persons_stored_report_is_never_anothers(
        self,
        client: TestClient,
        user_headers: dict[str, str],
        user_b_headers: dict[str, str],
        provider: RecordingProvider,
        model: Counter,
    ) -> None:
        """The cache key is scoped to the user, like every other read here."""

        client.get("/reports/activity", params=closed_params(), headers=user_headers)
        model.calls.clear()

        other = client.get(
            "/reports/activity", params=closed_params(), headers=user_b_headers
        )

        assert other.json()["source"] == "generated"
        assert model.calls, "another user's report is not this user's cache"


def _age(db: Session, user_id: uuid.UUID, *, by: timedelta) -> None:
    """Make the stored report as old as a clock would have made it."""

    for row in activity_store.history(db, user_id=user_id):
        row.generated_at = (row.generated_at or datetime.now(UTC)) - by
        row.checked_at = (row.checked_at or datetime.now(UTC)) - by

    db.commit()
