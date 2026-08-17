"""The timer, tested without a sync attached.

The scheduler is given a plain callable, which is the point of keeping it
separate from the sync service: what it guarantees — that it fires, that it
survives a failure, that it stops — has nothing to do with OneDrive.

`asyncio.run` rather than an async-test plugin. The suite's standing rule is
that it needs no services and no extra machinery, and one helper is cheaper
than a dependency.
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from app.services.features.sync.scheduler import SyncScheduler


def run(coroutine: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(coroutine())


def test_the_work_runs_on_a_tick() -> None:
    calls: list[int] = []

    async def scenario() -> None:
        await SyncScheduler(lambda: calls.append(1), interval_seconds=3600).tick()

    run(scenario)

    assert calls == [1]


def test_an_async_callable_is_awaited() -> None:
    calls: list[int] = []

    async def work() -> None:
        await asyncio.sleep(0)
        calls.append(1)

    async def scenario() -> None:
        await SyncScheduler(work, interval_seconds=3600).tick()

    run(scenario)

    assert calls == [1]


def test_a_failing_tick_does_not_kill_the_scheduler() -> None:
    """A scheduled job that dies on its first bad night is worse than one that
    logs and tries again: the usual cause is the far end being briefly
    unavailable, and the sync state is built to make a retry safe."""

    def work() -> None:
        raise RuntimeError("Graph is down.")

    async def scenario() -> None:
        await SyncScheduler(work, interval_seconds=3600).tick()

    run(scenario)  # must not raise


def test_starting_runs_the_work_repeatedly() -> None:
    calls: list[int] = []

    async def scenario() -> None:
        scheduler = SyncScheduler(
            lambda: calls.append(1), interval_seconds=1, run_immediately=True
        )
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

    run(scenario)

    assert calls


def test_stopping_ends_the_loop() -> None:
    states: list[bool] = []

    async def scenario() -> None:
        scheduler = SyncScheduler(lambda: None, interval_seconds=1)
        await scheduler.start()
        states.append(scheduler.running)
        await scheduler.stop()
        states.append(scheduler.running)

    run(scenario)

    assert states == [True, False]


def test_stopping_does_not_wait_out_the_whole_interval() -> None:
    """Shutdown waits on the stop event rather than sleeping, so a deployment
    with an hourly sync does not take an hour to restart."""

    async def scenario() -> None:
        scheduler = SyncScheduler(lambda: None, interval_seconds=3600)
        await scheduler.start()
        await asyncio.wait_for(scheduler.stop(), timeout=2)

    run(scenario)


def test_starting_twice_does_not_create_a_second_loop() -> None:
    calls: list[int] = []

    async def scenario() -> None:
        scheduler = SyncScheduler(
            lambda: calls.append(1), interval_seconds=1, run_immediately=True
        )
        await scheduler.start()
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

    run(scenario)

    assert len(calls) <= 2


def test_stopping_before_starting_is_harmless() -> None:
    async def scenario() -> None:
        await SyncScheduler(lambda: None, interval_seconds=1).stop()

    run(scenario)


def test_a_non_positive_interval_is_rejected() -> None:
    """A zero interval is a busy loop against somebody else's API."""

    with pytest.raises(ValueError, match="greater than zero"):
        SyncScheduler(lambda: None, interval_seconds=0)


def test_a_tick_runs_an_incremental_sync_not_a_full_one() -> None:
    """What the deployment wires up. A scheduled `full=True` would re-enumerate
    every folder every hour, which is the cost delta queries exist to avoid."""

    from app import main

    calls: list[dict] = []

    def fake_sync_all(_db, **kwargs):
        calls.append(kwargs)
        return []

    class FakeSession:
        def close(self) -> None:
            return None

    import app.services.features.sync.onedrive_sync_service as sync_module
    from app.database import database as database_module

    original_sync = sync_module.sync_all
    original_session = database_module.SessionLocal

    sync_module.sync_all = fake_sync_all  # type: ignore[assignment]
    database_module.SessionLocal = FakeSession  # type: ignore[assignment]
    try:
        main._run_scheduled_sync()
    finally:
        sync_module.sync_all = original_sync  # type: ignore[assignment]
        database_module.SessionLocal = original_session  # type: ignore[assignment]

    assert calls == [{}]
