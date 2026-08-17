"""Running the incremental sync on a timer.

Deliberately separate from the sync service: the scheduler decides *when*, the
service decides *what*, and neither needs the other to be testable. The
service can be driven from a request or a test with no scheduler present, and
this can be exercised with a callable that is not a sync at all.

An asyncio task rather than Celery or a cron container. The work is one
periodic call in a process that is already running an event loop, and the
project's standing rule is that a second piece of infrastructure needs to earn
its place. What this gives up is recorded in docs/KNOWN_ISSUES.md: with more
than one worker process, every worker runs its own timer.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

SyncCallable = Callable[[], Awaitable[None]] | Callable[[], None]


class SyncScheduler:
    """Calls `run` every `interval_seconds` until stopped."""

    def __init__(
        self,
        run: SyncCallable,
        *,
        interval_seconds: int,
        run_immediately: bool = False,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero.")

        self._run = run
        self._interval = interval_seconds
        self._run_immediately = run_immediately
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return

        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="onedrive-sync")

        logger.info("sync_scheduler_started", extra={"interval": self._interval})

    async def stop(self) -> None:
        """Ask the loop to finish, and wait for the current tick to end.

        Waiting rather than cancelling: a tick that is halfway through a sync
        has an open transaction and a staged file, and killing it mid-flight
        is how temporary files get left behind.
        """

        if self._task is None:
            return

        self._stopping.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._interval)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

        logger.info("sync_scheduler_stopped")

    async def tick(self) -> None:
        """Run the sync once, swallowing anything it raises.

        A scheduled job that dies on its first bad night is worse than one
        that logs and tries again: the failure is usually the far end being
        briefly unavailable, and the state is designed to make a retry safe.
        """

        try:
            result = self._run()

            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - the loop must survive its own work
            logger.exception("scheduled_sync_failed")

    async def _loop(self) -> None:
        if self._run_immediately:
            await self.tick()

        while not self._stopping.is_set():
            try:
                # Waiting on the stop event rather than sleeping means a
                # shutdown does not have to sit through a full interval.
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
            except TimeoutError:
                pass

            if self._stopping.is_set():
                return

            await self.tick()
