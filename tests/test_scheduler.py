"""Tests for the scheduler wrapper and the daily meal job.

The job execution is tested directly (no waiting for 07:00) and the
scheduler lifecycle (start/stop) is verified against the real APScheduler.
"""

from __future__ import annotations

import logging

import pytest

from core.scheduler import Scheduler
from features.meal.parser import MealResult
from features.meal.scheduler import MEAL_JOB_ID, MealScheduler


def test_meal_job_registered_daily_0700() -> None:
    scheduler = Scheduler(timezone="Asia/Seoul")
    meal = MealScheduler(scheduler, service=object())
    meal.register()

    job = scheduler.backend.get_job(MEAL_JOB_ID)
    assert job is not None
    # APScheduler's CronTrigger stores values as fields; assert on its repr.
    assert "hour='7'" in repr(job.trigger)
    assert "minute='0'" in repr(job.trigger)
    assert str(job.trigger.timezone) == "Asia/Seoul"


def test_meal_job_register_is_idempotent() -> None:
    scheduler = Scheduler()
    meal = MealScheduler(scheduler, service=object())
    meal.register()
    meal.register()

    jobs = [job for job in scheduler.backend.get_jobs() if job.id == MEAL_JOB_ID]
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_meal_job_run_success(caplog: pytest.LogCaptureFixture) -> None:
    class FakeService:
        async def send_meal(self, today: str | None = None) -> MealResult:
            return MealResult(date="20260806", menu="밥\n국", calories="500")

        def default_date(self) -> str:
            return "20260806"

    scheduler = Scheduler()
    meal = MealScheduler(scheduler, FakeService())

    with caplog.at_level(logging.INFO, logger="features.meal.scheduler"):
        await meal._run()

    messages = [record.getMessage() for record in caplog.records]
    assert any("Meal Fetch Started" in message for message in messages)
    assert any("Meal Fetch Success" in message for message in messages)


@pytest.mark.asyncio
async def test_meal_job_exception_handled(caplog: pytest.LogCaptureFixture) -> None:
    class FailingService:
        async def send_meal(self, today: str | None = None) -> MealResult:
            raise RuntimeError("boom")

        def default_date(self) -> str:
            return "20260806"

    scheduler = Scheduler()
    meal = MealScheduler(scheduler, FailingService())

    with caplog.at_level(logging.ERROR, logger="features.meal.scheduler"):
        await meal._run()  # must not raise / crash the bot

    messages = [record.getMessage() for record in caplog.records]
    assert any("Meal Fetch Failed" in message for message in messages)


@pytest.mark.asyncio
async def test_scheduler_start_and_stop() -> None:
    scheduler = Scheduler(timezone="Asia/Seoul")
    assert not scheduler.backend.running

    await scheduler.start()
    assert scheduler.backend.running

    await scheduler.shutdown()
    assert not scheduler.backend.running
