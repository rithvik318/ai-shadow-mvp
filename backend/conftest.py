"""Isolating the test suite from the machine it runs on.

This file exists at the rootdir rather than under `tests/` for one reason:
pytest imports it before it imports anything else, which is the only moment at
which the environment can still be neutralised. By the time a test module runs,
`app.config.settings` has been imported and `settings = Settings()` has already
read whatever `backend/.env` happened to contain.

**The failure this prevents.** A developer with a populated `.env` was running a
suite that silently read their production configuration. Three sync-route tests
assert that no source is configured — `body["sources"] == []` — and were instead
handed the real `knowledge-base` source, so they failed on a machine with
credentials and passed on one without. That is the worst shape a test can have:
it is not a bug in the endpoint, and chasing it leads to weakening the
assertion.

Worse was waiting behind it. With real `ONEDRIVE_*` credentials present,
`GraphClient.from_settings()` stops raising `SyncNotConfiguredError` and starts
succeeding, so any test reaching a code path that builds a client from
configuration would mint a real token and talk to the real tenant — reading a
real mailbox, or enumerating a real drive, from inside a unit test. CLAUDE.md §7
says tests never make network calls and never require credentials; this is what
makes that true rather than merely intended.

Nothing here changes how the application reads configuration. `Settings` still
defaults to `.env`, and a developer running `uvicorn` gets exactly what they
always did.
"""

import ipaddress
import os
import socket
from collections.abc import Iterator

import pytest

from app.config import settings as settings_module


def _isolate_settings() -> None:
    """Reset every setting to its in-code default, ignoring the environment.

    Two sources of leakage, both closed. `_env_file=None` tells
    pydantic-settings to skip `backend/.env` entirely, and popping each field
    name from `os.environ` closes the other half — a developer or a CI job with
    `ONEDRIVE_SOURCES` exported in the shell would otherwise leak just as
    surely, and that variant would be even harder to see.

    The field list comes from `Settings.model_fields` rather than being written
    out, so a setting added next month is isolated without anybody remembering
    to add it here.

    The live `settings` object is mutated in place rather than replaced.
    Every module does `from app.config.settings import settings`, binding the
    object itself, so rebinding the module attribute would update nothing —
    and mutation is already how the suite's own `monkeypatch.setattr` fixtures
    override individual values.
    """

    for name in settings_module.Settings.model_fields:
        os.environ.pop(name, None)

    pristine = settings_module.Settings(_env_file=None)

    for name in settings_module.Settings.model_fields:
        setattr(settings_module.settings, name, getattr(pristine, name))


_isolate_settings()


@pytest.fixture(autouse=True)
def isolated_settings() -> Iterator[None]:
    """Restore the defaults around every test, the way the prompt registry is.

    Both sides, deliberately. `monkeypatch.setattr` already undoes the
    overrides a test sets on purpose, so the teardown covers what it cannot —
    a test that assigns to `settings` directly, or a service that writes back
    to it, would otherwise hand the next test a value it never asked for, and
    the symptom of that is a suite that passes one test at a time and fails
    when run in order.

    The setup side is what makes the guarantee independent of ordering: with a
    teardown alone, the first test to run is still exposed to the environment,
    and "only the first test sees it" is the hardest version of this bug to
    reproduce. Autouse fixtures are set up before the explicitly requested ones
    at the same scope, so a test's own `monkeypatch` fixture still wins.
    """

    _isolate_settings()
    yield
    _isolate_settings()


class NetworkAccessInTest(RuntimeError):
    """A test tried to reach something outside this machine.

    Raised rather than allowed, because the alternative is what it replaced: a
    run that appears to hang. A real Graph call with a 60-second timeout looks
    exactly like a stuck test, and the person watching has no way to tell
    whether to wait.
    """


def _is_local(address: object) -> bool:
    """Whether this destination is on this machine.

    **Loopback is not the network, and blocking it broke the suite on Windows.**

    The first version of this guard refused every `AF_INET` connection. On Linux
    that was invisible: `socket.socketpair()` there is `AF_UNIX`, which the
    guard allowed, so all 1481 tests passed. On Windows `socketpair()` has no
    Unix sockets to use and is emulated with a loopback TCP pair — 127.0.0.1 on
    an ephemeral port. Every asyncio event loop builds its self-pipe from one,
    `TestClient` starts an event loop through anyio for every request, and so
    343 tests across chat, documents, email, reports, sync, tasks, users and
    calendar died on a socket that never left the machine.

    So the rule is about *destination*, not address family. A loopback address
    cannot reach Microsoft Graph or OpenAI, which is the entire thing this guard
    exists to prevent; a local Postgres, a self-pipe and a test fixture's own
    listener are all legitimate. Everything else still raises.
    """

    host: object = address

    if isinstance(address, tuple) and address:
        # IPv6 gives (host, port, flowinfo, scope_id); IPv4 gives (host, port).
        host = address[0]

    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")

    if not isinstance(host, str):
        # An address shape nothing here recognises. Refusing is the cautious
        # answer, and a genuinely local connection is always one of the forms
        # handled above.
        return False

    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        # A hostname rather than a literal. `localhost` is the only one that is
        # local by definition; anything else has to be resolved to be known,
        # and resolving it here would be a network call of its own.
        return host.lower() in {"localhost", "localhost.localdomain"}

    if parsed.version == 6 and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped

    return parsed.is_loopback


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any real network connection fail loudly and immediately.

    The suite is meant to run with no credentials and no services — that is
    what makes CI possible, and it is stated in CLAUDE.md §7. This enforces it
    instead of trusting it. Anything that genuinely needs to answer an HTTP
    request uses `httpx.MockTransport`, which never reaches this.

    `socket.socket.connect` is the chokepoint: it is below `httpx`, `openai`
    and anything else a service might reach for, so no library can route around
    it. Unix domain sockets and loopback are left alone — see `_is_local`.
    """

    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(
            address
        ):
            raise NetworkAccessInTest(
                f"A test tried to open a network connection to {address!r}. "
                "Tests run with no credentials and no services; mock the "
                "provider, or inject an httpx.MockTransport."
            )

        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)
