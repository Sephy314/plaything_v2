"""Async scheduler that runs periodic background jobs."""

from __future__ import annotations

import asyncio

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.logger import get_logger, log_event

log = get_logger(__name__)

DEFAULT_TIMEZONE = "Asia/Seoul"


class Scheduler:
    """Thin wrapper around :class:`AsyncIOScheduler`.

    Jobs are added by features/services through :meth:`add_job`. The scheduler
    timezone defaults to ``Asia/Seoul`` so cron triggers fire at Seoul wall
    time (e.g. the daily 07:00 meal job).
    """

    def __init__(self, timezone: str = DEFAULT_TIMEZONE) -> None:
        self._scheduler = AsyncIOScheduler(timezone=timezone)
        self._listener_attached = False

    @property
    def backend(self) -> AsyncIOScheduler:
        """Return the underlying scheduler instance."""
        return self._scheduler

    def add_job(self, func, trigger: str, **kwargs):
        """Register a periodic job.

        Args:
            func: The coroutine function to invoke.
            trigger: APScheduler trigger type, e.g. ``"interval"`` or ``"cron"``.
            **kwargs: Extra trigger/job options.

        Returns:
            The created :class:`apscheduler.job.Job`.
        """
        return self._scheduler.add_job(func, trigger=trigger, **kwargs)

    async def start(self) -> None:
        """Start the scheduler (no-op if already running)."""
        if not self._scheduler.running:
            self._attach_listener()
            self._scheduler.start()
            log_event(log, "Scheduler Started", details={"timezone": str(self._scheduler.timezone)})

    async def shutdown(self) -> None:
        """Gracefully stop the scheduler.

        APScheduler 3.11 defers the ``AsyncIOScheduler`` teardown onto the
        event loop, so we yield control until the state transition is visible
        before reporting shutdown.
        """
        if not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=False)
        for _ in range(100):
            if not self._scheduler.running:
                break
            await asyncio.sleep(0)
        log_event(log, "Scheduler Stopped")

    def _attach_listener(self) -> None:
        """Attach the job lifecycle listener exactly once."""
        if self._listener_attached:
            return
        self._scheduler.add_listener(self._on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
        self._listener_attached = True

    def _on_job_event(self, event) -> None:
        """Log job failures/misses so they never go unnoticed.

        APScheduler already catches job exceptions; this makes them visible as
        structured error events and keeps the bot alive.
        """
        if event.code == EVENT_JOB_ERROR:
            log.error(
                "scheduled job %s failed: %s",
                event.job_id,
                event.exception,
                exc_info=event.exception,
            )
        elif event.code == EVENT_JOB_MISSED:
            log.warning("scheduled job %s missed its execution window", event.job_id)


async def scheduler_heartbeat() -> None:
    """Periodic sanity job to prove the scheduler is alive."""
    log.info("Scheduler Alive")
