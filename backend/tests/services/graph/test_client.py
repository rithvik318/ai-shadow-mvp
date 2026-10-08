"""The Graph client, tested against a mock transport rather than a mock client.

The point of these is that the real request-building, token caching, paging
and error translation run. A hand-written double would test the double.
"""

import httpx
import pytest

from app.core.exceptions import (
    DeltaTokenExpiredError,
    GraphAuthError,
    GraphRequestError,
    SyncNotConfiguredError,
)
from app.services.graph.client import GraphClient
from tests.support.graph import TOKEN_URL_FRAGMENT, graph_transport


def _client(transport: httpx.MockTransport) -> GraphClient:
    return GraphClient(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        base_url="https://graph.example/v1.0",
        authority="https://login.example",
        transport=transport,
    )


# --- authentication ------------------------------------------------------


def test_a_token_is_acquired_before_the_first_call() -> None:
    client = _client(graph_transport({"/drives/": {"id": "x"}}))

    assert client.access_token() == "test-token"


def test_the_token_is_reused_rather_than_minted_per_request() -> None:
    """A token call per Graph call would triple the traffic and eventually be
    throttled by the identity provider rather than by Graph."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))

        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 3600}
            )

        return httpx.Response(200, json={"id": "x"})

    client = _client(httpx.MockTransport(handler))
    client.get("/drives/d/items/i")
    client.get("/drives/d/items/i")

    assert sum(1 for url in calls if TOKEN_URL_FRAGMENT in url) == 1


def test_an_expired_token_is_minted_again() -> None:
    """`expires_in` below the safety margin means the cache is already stale,
    which is the boundary worth pinning."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))

        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 1}
            )

        return httpx.Response(200, json={"id": "x"})

    client = _client(httpx.MockTransport(handler))
    client.get("/drives/d/items/i")
    client.get("/drives/d/items/i")

    assert sum(1 for url in calls if TOKEN_URL_FRAGMENT in url) == 2


def test_invalidating_the_token_forces_a_new_one() -> None:
    client = _client(graph_transport({"/drives/": {"id": "x"}}))

    client.access_token()
    client.invalidate_token()

    assert client.access_token() == "test-token"


def test_rejected_credentials_raise_an_auth_error() -> None:
    client = _client(graph_transport({}, token_status=401))

    with pytest.raises(GraphAuthError):
        client.access_token()


def test_the_auth_error_does_not_echo_the_credential() -> None:
    """An exception message can reach a log or a response body, and the thing
    that failed to authenticate is a secret."""

    client = _client(graph_transport({}, token_status=401))

    with pytest.raises(GraphAuthError) as error:
        client.access_token()

    assert "secret" not in str(error.value)


def test_missing_configuration_is_named_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Isolated from the environment on purpose: a configured .env would make
    # from_settings() correctly succeed, proving nothing about this path.
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_TENANT_ID", None)
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_ID", None)
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_SECRET", None)

    with pytest.raises(SyncNotConfiguredError) as error:
        GraphClient.from_settings()

    assert "ONEDRIVE_TENANT_ID" in str(error.value)


def test_configuration_builds_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_TENANT_ID", "t")
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_ID", "c")
    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_CLIENT_SECRET", "s")

    assert GraphClient.from_settings() is not None


# --- requests and paging -------------------------------------------------


def test_a_relative_path_is_resolved_against_the_base_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        seen.append(str(request.url))
        return httpx.Response(200, json={})

    _client(httpx.MockTransport(handler)).get("/drives/d/root")

    assert seen == ["https://graph.example/v1.0/drives/d/root"]


def test_paging_follows_next_link_to_the_end() -> None:
    """Graph pages with an absolute URL rather than an offset, and following
    it is what keeps paging correct while the collection is changing."""

    pages = {
        "page=2": {"value": [{"id": "c"}]},
        "/children": {
            "value": [{"id": "a"}, {"id": "b"}],
            "@odata.nextLink": "https://graph.example/v1.0/next?page=2",
        },
    }

    client = _client(graph_transport(pages))
    items = list(client.paged("/drives/d/items/i/children"))

    assert [item["id"] for item in items] == ["a", "b", "c"]


def test_delta_returns_every_page_and_the_final_link() -> None:
    pages = {
        "page=2": {
            "value": [{"id": "c"}],
            "@odata.deltaLink": "https://d/delta?token=2",
        },
        "/delta": {
            "value": [{"id": "a"}],
            "@odata.nextLink": "https://graph.example/v1.0/next?page=2",
        },
    }

    items, link = _client(graph_transport(pages)).delta("/drives/d/items/i/delta")

    assert [item["id"] for item in items] == ["a", "c"]
    assert link == "https://d/delta?token=2"


def test_a_delta_link_only_appears_on_the_last_page() -> None:
    """Committing a link from an intermediate page would skip the pages after
    it on the next run."""

    pages = {
        "/delta": {
            "value": [{"id": "a"}],
            "@odata.nextLink": "https://graph.example/v1.0/next?page=2",
        },
        "page=2": {"value": [], "@odata.deltaLink": "https://d/final"},
    }

    _items, link = _client(graph_transport(pages)).delta("/drives/d/items/i/delta")

    assert link == "https://d/final"


# --- error translation ---------------------------------------------------


def test_a_gone_response_becomes_a_delta_expiry() -> None:
    transport = graph_transport(
        {"/delta": (410, {"error": {"code": "resyncRequired"}})}
    )

    with pytest.raises(DeltaTokenExpiredError):
        _client(transport).get("/drives/d/items/i/delta")


def test_a_resync_code_becomes_a_delta_expiry_whatever_the_status() -> None:
    transport = graph_transport(
        {"/delta": (400, {"error": {"code": "resyncApplyDifferences"}})}
    )

    with pytest.raises(DeltaTokenExpiredError):
        _client(transport).get("/drives/d/items/i/delta")


def test_a_forbidden_response_becomes_an_auth_error() -> None:
    """403 from Graph almost always means missing admin consent, so the error
    says so rather than leaving somebody to guess — but it does **not** guess
    *which* consent.

    This client serves files, mail and calendar, three separate admin consents,
    and it cannot see which one the caller was exercising. It used to assert
    `Files.Read.All` unconditionally, which was right for a sync and wrong
    everywhere else: the first real calendar 403 told the reader to re-grant a
    files permission that could not have been the cause. Naming the consent is
    the job of the layer that knows which API it called.
    """

    transport = graph_transport(
        {"/drives/": (403, {"error": {"code": "accessDenied"}})}
    )

    with pytest.raises(GraphAuthError) as error:
        _client(transport).get("/drives/d/root")

    message = str(error.value)

    assert "403" in message
    assert "accessDenied" in message
    assert "granted per API" in message
    assert "Files.Read.All" not in message


def test_an_auth_error_carries_what_graph_answered() -> None:
    """Structured, so a caller can compose an accurate remedy.

    Parsing the sentence back out would be the alternative, and a message is
    not an interface.
    """

    transport = graph_transport(
        {"/drives/": (403, {"error": {"code": "accessDenied"}})}
    )

    with pytest.raises(GraphAuthError) as error:
        _client(transport).get("/drives/d/root")

    assert error.value.status_code == 403
    assert error.value.graph_code == "accessDenied"
    # Missing consent does not heal on a schedule.
    assert error.value.transient is False


def test_other_failures_become_request_errors() -> None:
    transport = graph_transport({"/drives/": (500, {"error": {"code": "internal"}})})

    with pytest.raises(GraphRequestError):
        _client(transport).get("/drives/d/root")


def test_an_error_never_carries_the_query_string() -> None:
    """Delta and download tokens live in the query. An exception message can
    end up in a log or a response body, and neither is a place for one."""

    transport = graph_transport({"/items/": (500, {"error": {"code": "internal"}})})

    with pytest.raises(GraphRequestError) as error:
        _client(transport).get("/drives/d/items/i?token=super-secret")

    assert "super-secret" not in str(error.value)


def test_a_transport_failure_becomes_a_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        raise httpx.ConnectError("no route to host")

    with pytest.raises(GraphRequestError):
        _client(httpx.MockTransport(handler)).get("/drives/d/root")


# --- downloads -----------------------------------------------------------


def test_a_file_is_downloaded_by_its_pre_authorised_url() -> None:
    transport = graph_transport({}, downloads={"files.example": b"the bytes"})

    assert _client(transport).download("https://files.example/x") == b"the bytes"


def test_a_download_does_not_carry_the_bearer_token() -> None:
    """Graph's download URLs are pre-authorised and point at storage hosts.
    Attaching the token would hand it to somewhere that does not need it."""

    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        headers.append(request.headers)
        return httpx.Response(200, content=b"x")

    _client(httpx.MockTransport(handler)).download("https://files.example/x")

    assert all("authorization" not in header for header in headers)


def test_a_failed_download_raises() -> None:
    transport = graph_transport({})

    with pytest.raises(GraphRequestError):
        _client(transport).download("https://files.example/missing")


# --- throttling ----------------------------------------------------------
#
# Graph throttles a large corpus aggressively, and a first sync over ~786
# documents is exactly the traffic that triggers it. A 429 treated as a failed
# file would hold the delta token back permanently and the corpus would never
# finish, so these pin the retry behaviour rather than the error message.


def _throttle_then(status_after: int, *, retry_after: str | None, times: int = 1):
    """A transport that throttles `times` times, then answers normally."""

    seen: list[float] = []
    remaining = {"n": times}

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        seen.append(0.0)

        if remaining["n"] > 0:
            remaining["n"] -= 1
            headers = {"Retry-After": retry_after} if retry_after is not None else {}
            return httpx.Response(
                429, json={"error": {"code": "activityLimitReached"}}, headers=headers
            )

        return httpx.Response(status_after, json={"id": "x"})

    return httpx.MockTransport(handler), seen


def test_a_throttled_request_is_retried_rather_than_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr("app.services.graph.client.time.sleep", slept.append)

    transport, attempts = _throttle_then(200, retry_after="0.01")

    assert _client(transport).get("/drives/d/root") == {"id": "x"}
    assert len(attempts) == 2


def test_the_retry_after_header_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Graph is telling us its own rate; guessing instead would keep tripping
    the same limit."""

    slept: list[float] = []
    monkeypatch.setattr("app.services.graph.client.time.sleep", slept.append)

    transport, _ = _throttle_then(200, retry_after="7")
    _client(transport).get("/drives/d/root")

    assert slept == [7.0]


def test_a_missing_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr("app.services.graph.client.time.sleep", slept.append)

    transport, _ = _throttle_then(200, retry_after=None)
    _client(transport).get("/drives/d/root")

    assert slept and slept[0] > 0


def test_an_absurd_retry_after_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """One hostile or mistaken header must not stall a sync run for an hour."""

    slept: list[float] = []
    monkeypatch.setattr("app.services.graph.client.time.sleep", slept.append)

    transport, _ = _throttle_then(200, retry_after="99999")
    _client(transport).get("/drives/d/root")

    assert slept == [60.0]


def test_persistent_throttling_eventually_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past a point the far end is down, not busy, and the caller should hear
    about it rather than block forever."""

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    transport, attempts = _throttle_then(200, retry_after="0", times=99)

    with pytest.raises(GraphRequestError):
        _client(transport).get("/drives/d/root")

    assert len(attempts) == 5


def test_a_transient_server_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        calls.append(1)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"id": "x"})

    assert _client(httpx.MockTransport(handler)).get("/drives/d/root") == {"id": "x"}
    assert len(calls) == 2


def test_an_ordinary_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 is an answer. Repeating it is just slower."""

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        calls.append(1)
        return httpx.Response(500, json={"error": {"code": "internal"}})

    with pytest.raises(GraphRequestError):
        _client(httpx.MockTransport(handler)).get("/drives/d/root")

    assert len(calls) == 1


def test_downloads_are_throttled_politely_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large corpus is throttled on its downloads as readily as its listings."""

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)

        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})

        return httpx.Response(200, content=b"the bytes")

    assert (
        _client(httpx.MockTransport(handler)).download("https://files.example/x")
        == b"the bytes"
    )
    assert len(calls) == 2


def test_a_throttle_log_does_not_carry_the_query_string(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Delta and download tokens live in the query, and a throttle is the one
    path that deliberately logs the URL."""

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    transport, _ = _throttle_then(200, retry_after="0")

    with caplog.at_level("WARNING"):
        _client(transport).get("/drives/d/items/i?token=super-secret")

    assert "super-secret" not in caplog.text


# --- download classification ---------------------------------------------
#
# One bit — can this succeed next time — decides whether a synchronisation
# withholds its delta token. Everything below pins one side of that line, and
# every one of them describes a failure a real 847-file run actually met.


def _download_transport(statuses: list[int], *, body: bytes = b"the bytes"):
    """Answer each download attempt with the next status in the list."""

    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if TOKEN_URL_FRAGMENT in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        if "/drives/" in url:
            return httpx.Response(
                200,
                json={
                    "id": "i1",
                    "@microsoft.graph.downloadUrl": "https://files.example/fresh",
                },
            )

        attempts.append(url)
        status = statuses[min(len(attempts) - 1, len(statuses) - 1)]

        if status == 200:
            return httpx.Response(200, content=body)

        return httpx.Response(status, json={"error": {"code": "x"}})

    return httpx.MockTransport(handler), attempts


def test_an_expired_download_url_is_re_minted_and_retried() -> None:
    """Graph's pre-authorised URLs live about an hour.

    A sync that enumerates a folder and then downloads it is holding URLs from
    before it started. Without this, every file reached after the first hour
    failed — and the 403 read as a permissions problem rather than as a clock.
    """

    transport, attempts = _download_transport([403, 200])
    client = _client(transport)

    data = client.download(
        "https://files.example/stale", refresh=lambda: "https://files.example/fresh"
    )

    assert data == b"the bytes"
    assert attempts == ["https://files.example/stale", "https://files.example/fresh"]


def test_a_refreshed_url_that_is_still_refused_is_permanent() -> None:
    # The application genuinely cannot read this item. Retrying forever would
    # hold the delta token back over a file nothing can open.
    transport, attempts = _download_transport([403, 403])

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).download(
            "https://files.example/stale", refresh=lambda: "https://files.example/fresh"
        )

    assert caught.value.transient is False
    assert len(attempts) == 2


def test_a_download_with_no_refresher_does_not_invent_one() -> None:
    transport, attempts = _download_transport([403])

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).download("https://files.example/stale")

    assert caught.value.transient is False
    assert len(attempts) == 1


def test_a_missing_file_is_permanent() -> None:
    transport, _ = _download_transport([404])

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).download("https://files.example/gone")

    assert caught.value.transient is False


def test_an_exhausted_throttle_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    """The content was never seen, so the token must not move past it."""

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    transport, _ = _download_transport([429])

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).download("https://files.example/busy")

    assert caught.value.transient is True


def test_a_server_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)

    transport, _ = _download_transport([500])

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).download("https://files.example/broken")

    assert caught.value.transient is True


def test_a_refusal_from_graph_itself_is_permanent() -> None:
    """Missing admin consent does not heal on a schedule.

    Retrying it nightly produces the same answer more often, which is how a
    sync ends up re-walking a folder forever to collect one 403.
    """

    transport = graph_transport({"/root": (403, {"error": {"code": "accessDenied"}})})

    with pytest.raises(GraphAuthError) as caught:
        _client(transport).get("/drives/d/root")

    assert caught.value.transient is False


def test_the_total_retry_budget_bounds_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-attempt caps do not bound a run; the total does.

    Five attempts each honouring a 60-second `Retry-After` is five minutes
    inside one file. Several hundred throttled files turn that into a sync
    measured in days — which is most of where 25 hours went.
    """

    slept: list[float] = []
    monkeypatch.setattr("app.services.graph.client.time.sleep", slept.append)

    transport, attempts = _throttle_then(200, retry_after="60", times=99)

    with pytest.raises(GraphRequestError):
        _client(transport).get("/drives/d/root")

    assert sum(slept) <= 120.0
    # It gave up on the budget rather than on the attempt count.
    assert len(attempts) < 5


# --- transport errors ----------------------------------------------------
#
# A dropped read is the most literal "not now" there is: no status came back at
# all. It was nevertheless the one condition this client did not retry, so a
# single `ReadError` on a single file marked an 800-file run `partial` and
# withheld its delta token — a whole re-enumeration paid for a hiccup. That is
# what happened to `Sun Radia Capabilities Case Studies and Services.pptx`.


def _flaky_transport(failures: int, *, error: type[Exception] = httpx.ReadError):
    """A transport that raises `failures` times, then answers normally."""

    state = {"remaining": failures}

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 3600}
            )

        if state["remaining"] > 0:
            state["remaining"] -= 1
            raise error("connection dropped")

        return httpx.Response(200, json={"id": "recovered"})

    return httpx.MockTransport(handler), state


def test_a_dropped_read_is_retried_rather_than_failing_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)
    transport, state = _flaky_transport(1)

    assert _client(transport).get("/drives/d/items/i") == {"id": "recovered"}
    assert state["remaining"] == 0


def test_the_retry_backs_off_rather_than_hammering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no `Retry-After` to honour, so the fallback has to be used."""

    slept: list[float] = []
    monkeypatch.setattr("app.services.graph.client.time.sleep", slept.append)
    transport, _ = _flaky_transport(2)

    _client(transport).get("/drives/d/items/i")

    assert slept == sorted(slept)
    assert all(wait > 0 for wait in slept)


def test_a_connection_error_is_retried_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)
    transport, _ = _flaky_transport(1, error=httpx.ConnectError)

    assert _client(transport).get("/drives/d/items/i") == {"id": "recovered"}


def test_a_transport_that_never_recovers_is_still_reported_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bytes were never seen, so the delta token must not move past it.

    `transient` is the single bit that decides whether the token advances, and
    getting it wrong here would bury the file rather than merely delay it.
    """

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)
    transport, _ = _flaky_transport(99)

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).get("/drives/d/items/i")

    assert caught.value.transient is True


def test_the_failure_says_how_many_attempts_were_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)
    transport, _ = _flaky_transport(99)

    with pytest.raises(GraphRequestError) as caught:
        _client(transport).get("/drives/d/items/i")

    assert "attempt(s)" in str(caught.value)


def test_a_download_that_drops_mid_read_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path the pptx actually failed on."""

    monkeypatch.setattr("app.services.graph.client.time.sleep", lambda _: None)
    state = {"remaining": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if TOKEN_URL_FRAGMENT in str(request.url):
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 3600}
            )

        if state["remaining"] > 0:
            state["remaining"] -= 1
            raise httpx.ReadError("connection dropped")

        return httpx.Response(200, content=b"slide bytes")

    assert (
        _client(httpx.MockTransport(handler)).download("https://files.example/x")
        == b"slide bytes"
    )
