"""Daily meal scheduler.

Registers a cron job on the shared :class:`Scheduler` that runs every day at
07:00 Asia/Seoul, fetches the school meal and posts it to the configured
Discord channel. Every failure (HTTP error, timeout, parser error, Discord
send error) is caught and logged so the bot never crashes.
"""

from __future__ import annotations

import logging

from core.logger import get_logger, log_event

log = get_logger(__name__)

MEAL_HOUR = 7
MEAL_MINUTE = 0
MEAL_TIMEZONE = "Asia/Seoul"
MEAL_JOB_ID = "meal_daily"


class MealScheduler:
    """Owns the daily meal job registration and execution.

    Args:
        scheduler: The shared :class:`Scheduler` instance.
        service: The :class:`MealService` used to fetch and send meals.
        timezone: IANA timezone for the cron trigger (default Asia/Seoul).
    """

    def __init__(self, scheduler, service, *, timezone: str = MEAL_TIMEZONE) -> None:
        self._scheduler = scheduler
        self._service = service
        self._timezone = timezone

    def register(self) -> None:
        """Register the daily meal job (idempotent)."""
        if self._scheduler.backend.get_job(MEAL_JOB_ID) is not None:
            return
        self._scheduler.add_job(
            self._run,
            "cron",
            id=MEAL_JOB_ID,
            hour=MEAL_HOUR,
            minute=MEAL_MINUTE,
            timezone=self._timezone,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info(
            "registered meal job: daily %02d:%02d %s",
            MEAL_HOUR,
            MEAL_MINUTE,
            self._timezone,
        )

    async def _run(self) -> None:
        """Scheduler entry point: fetch and publish today's meal."""
        today = self._service.default_date()
        log_event(log, "Meal Fetch Started", details={"date": today})
        try:
            result = await self._service.send_meal(today)
        except Exception as exc:
            log_event(
                log,
                "Meal Fetch Failed",
                level=logging.ERROR,
                date=today,
                error=str(exc),
            )
            log.error("meal fetch failed: %s", exc, exc_info=exc)
            return

        log_event(
            log,
            "Meal Fetch Success",
            details={"date": today, "has_meal": "yes" if result is not None else "no"},
        )
        if result is not None:
            log.info("meal published for date %s", today)
