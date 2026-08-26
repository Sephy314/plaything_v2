"""Daily meal scheduler.

Registers a cron job on the shared :class:`Scheduler` that runs every day at
07:00 Asia/Seoul, fetches the school meal and posts it to the configured
Discord channel. Every failure (HTTP error, timeout, parser error, Discord
send error) is caught and logged so the bot never crashes.
"""

from __future__ import annotations

import asyncio
import logging

from core.logger import get_logger, log_event

log = get_logger(__name__)

MEAL_HOUR = 7
MEAL_MINUTE = 0
MEAL_TIMEZONE = "Asia/Seoul"
MEAL_JOB_ID = "meal_daily"
#: Hard cap for the whole fetch + publish step. Without it, a stalled NEIS or
#: Discord request keeps the job "running" forever, and because the job uses
#: ``max_instances=1`` every later daily run gets skipped until the bot is
#: restarted — which looks exactly like "the meal cron stopped posting".
MEAL_SEND_TIMEOUT = 60


class MealScheduler:
    """Owns the daily meal job registration and execution.

    Args:
        scheduler: The shared :class:`Scheduler` instance.
        service: The :class:`MealService` used to fetch and send meals.
        timezone: IANA timezone for the cron trigger (default Asia/Seoul).
    """

    def __init__(
        self,
        scheduler,
        service,
        *,
        timezone: str = MEAL_TIMEZONE,
        timeout: float = MEAL_SEND_TIMEOUT,
    ) -> None:
        self._scheduler = scheduler
        self._service = service
        self._timezone = timezone
        self._timeout = timeout

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
            result = await asyncio.wait_for(self._service.send_meal(today), timeout=self._timeout)
        except TimeoutError:
            # A stalled request must NOT leave the job hanging: with
            # max_instances=1 the scheduler would skip every later run.
            log_event(
                log,
                "Meal Fetch Timeout",
                level=logging.ERROR,
                date=today,
                error=f"send_meal exceeded {self._timeout}s",
            )
            log.error(
                "meal fetch timed out after %ss — job recovered so future runs are not skipped",
                self._timeout,
            )
            return
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
