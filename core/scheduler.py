"""Async scheduler that runs periodic background jobs."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.logger import get_logger

log = get_logger(__name__)


class Scheduler:
    """Thin wrapper around :class:`AsyncIOScheduler`.

    Jobs are added by features/services through :meth:`add_job`.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    @property
    def backend(self) -> AsyncIOScheduler:
        """Return the underlying scheduler instance."""
        return self._scheduler

    def add_job(self, func, trigger: str, **kwargs) -> None:
        """Register a periodic job.

        Args:
            func: The coroutine function to invoke.
            trigger: APScheduler trigger type, e.g. ``"interval"``.
            **kwargs: Extra trigger/job options.
        """
        self._scheduler.add_job(func, trigger=trigger, **kwargs)

    async def start(self) -> None:
        """Start the scheduler (no-op if already running)."""
        if not self._scheduler.running:
            self._scheduler.start()
            log.info("scheduler started")

    async def shutdown(self) -> None:
        """Gracefully stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler stopped")


async def scheduler_heartbeat() -> None:
    """Periodic sanity job to prove the scheduler is alive."""
    log.info("Scheduler Alive")
