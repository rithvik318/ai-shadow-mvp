"""The suite must not be able to see the machine it runs on.

These assert the guarantees `conftest.py` sets up. They exist because the bug
they describe was invisible in exactly the way that costs the most time: a
developer with a populated `backend/.env` saw three sync-route failures that
nobody else could reproduce, and the failures pointed at the endpoint rather
than at the environment.

Every test here fails loudly if the isolation is removed, which is the only
reason it is worth having — an isolation nobody checks is an isolation that
quietly stops working the next time somebody adds a setting.
"""

import socket

import pytest

from app.config import settings as settings_module
from app.services.features.sync.source_config import load_sources


class TestConfigurationIsolation:
    def test_no_onedrive_source_is_configured(self) -> None:
        """The exact leak that broke three sync-route tests.

        A developer's real `knowledge-base` source was reaching an endpoint
        whose test asserts that nothing is configured. The endpoint was right;
        the environment was not supposed to be there.
        """

        assert settings_module.settings.ONEDRIVE_SOURCES == ""
        assert load_sources() == []

    def test_no_credential_reaches_the_suite(self) -> None:
        """With these set, `GraphClient.from_settings()` stops raising and
        starts succeeding — so a unit test would mint a real token and read a
        real tenant. That is worse than a failing assertion."""

        assert settings_module.settings.ONEDRIVE_TENANT_ID is None
        assert settings_module.settings.ONEDRIVE_CLIENT_ID is None
        assert settings_module.settings.ONEDRIVE_CLIENT_SECRET is None
        assert settings_module.settings.OPENAI_API_KEY is None
        assert settings_module.settings.OPENROUTER_API_KEY is None

    def test_the_scheduler_is_off(self) -> None:
        # A developer who has switched on a background sync must not have one
        # started by their test run.
        assert settings_module.settings.ONEDRIVE_SYNC_ENABLED is False

    def test_no_mailbox_is_configured(self) -> None:
        assert settings_module.settings.EMAIL_MAILBOX_ADDRESS is None
        assert settings_module.settings.EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX is False

    def test_every_setting_holds_its_in_code_default(self) -> None:
        """The general form of all of the above, and the one that keeps working
        when somebody adds a setting next month.

        Checked against a freshly built `Settings(_env_file=None)` rather than
        against a written-out list, so there is nothing to remember to update.
        """

        pristine = settings_module.Settings(_env_file=None)
        live = settings_module.settings

        differing = [
            name
            for name in settings_module.Settings.model_fields
            if getattr(live, name) != getattr(pristine, name)
        ]

        assert differing == []


class TestOverridesStillWork:
    """Isolation must not make the suite's own overrides impossible.

    Tests that need a configured source set one deliberately, and that has to
    keep working — otherwise the fix for the leak would break every test that
    was doing the right thing already.
    """

    def test_a_test_can_still_configure_a_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            settings_module.settings,
            "ONEDRIVE_SOURCES",
            '[{"key": "k", "label": "K", "drive_id": "d", "path": "p"}]',
        )

        assert [source.key for source in load_sources()] == ["k"]

    def test_the_override_does_not_survive_into_the_next_test(self) -> None:
        # Runs after the test above. If the reset were missing, this would see
        # that source — which is the failure mode where a suite passes one test
        # at a time and fails when run in order.
        assert load_sources() == []


class TestNetworkIsBlocked:
    def test_opening_a_socket_fails_loudly_rather_than_hanging(self) -> None:
        """A real Graph call has a 60-second timeout and looks exactly like a
        stuck test. Failing immediately, with the address in the message, is
        the difference between a diagnosis and a guess."""

        from conftest import NetworkAccessInTest

        # 192.0.2.0/24 is RFC 5737 TEST-NET-1 and is guaranteed unroutable, so
        # this needs no DNS and cannot depend on what the machine can reach.
        with pytest.raises(NetworkAccessInTest, match="192.0.2.1"):
            socket.create_connection(("192.0.2.1", 443), timeout=1)

    def test_an_http_client_cannot_reach_the_internet(self) -> None:
        import httpx

        from conftest import NetworkAccessInTest

        with pytest.raises((NetworkAccessInTest, httpx.HTTPError)):
            httpx.get("https://graph.microsoft.com/v1.0/me", timeout=1)

    def test_a_loopback_connection_is_allowed(self) -> None:
        """The regression that cost 343 tests, pinned on every platform.

        The first version of this guard refused every `AF_INET` connection.
        `socket.socketpair()` is `AF_UNIX` on Linux and was allowed; on Windows
        it has no Unix sockets to use and is emulated with a loopback TCP pair,
        so it was refused. Every asyncio event loop builds its self-pipe from
        one and `TestClient` starts an event loop per request, so the whole API
        suite died on Windows and nothing at all failed on Linux.

        This connects to a real listener on 127.0.0.1, which is the exact shape
        of that emulation, and it fails on any platform if the rule goes back to
        being about address family.
        """

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)

            with socket.create_connection(server.getsockname(), timeout=2) as client:
                accepted, _ = server.accept()
                accepted.close()
                assert client.fileno() != -1

    def test_a_socket_pair_can_be_created(self) -> None:
        """What asyncio actually calls, on the platform it actually runs on.

        `AF_UNIX` here, loopback TCP on Windows — the contract this asserts is
        that the guard permits whichever one the platform provides.
        """

        left, right = socket.socketpair()

        try:
            left.sendall(b"ping")
            assert right.recv(4) == b"ping"
        finally:
            left.close()
            right.close()

    def test_an_event_loop_can_start(self) -> None:
        """`TestClient` starts one of these for every request it serves.

        On Windows the loop's self-pipe is a loopback socket pair, so a guard
        that refuses loopback refuses the entire API test suite — which is what
        happened, one platform at a time.
        """

        import asyncio

        async def answer() -> str:
            await asyncio.sleep(0)
            return "ready"

        assert asyncio.run(answer()) == "ready"

    def test_localhost_by_name_is_also_local(self) -> None:
        # asyncio and anyio both use the literal, but a fixture spinning up a
        # local server may well use the name.
        from conftest import _is_local

        assert _is_local(("localhost", 5432)) is True
        assert _is_local(("127.0.0.1", 5432)) is True
        assert _is_local(("127.53.1.9", 80)) is True
        assert _is_local(("::1", 80, 0, 0)) is True
        assert _is_local(("::ffff:127.0.0.1", 80)) is True

    def test_everything_that_is_not_loopback_is_still_refused(self) -> None:
        """The guard's whole purpose, unchanged by the loopback exemption."""

        from conftest import _is_local

        assert _is_local(("20.190.129.1", 443)) is False  # Microsoft
        assert _is_local(("graph.microsoft.com", 443)) is False
        assert _is_local(("192.168.1.10", 443)) is False  # LAN is not local
        assert _is_local(("::", 443)) is False
        assert _is_local(None) is False

    def test_a_mock_transport_is_unaffected(self) -> None:
        # The guard must not break the way every other test talks HTTP.
        import httpx

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True})
        )

        with httpx.Client(transport=transport) as client:
            assert client.get("https://graph.example/v1.0/me").json() == {"ok": True}
