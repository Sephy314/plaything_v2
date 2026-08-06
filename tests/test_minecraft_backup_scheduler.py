"""Tests for the daily Minecraft backup scheduler.

The job registration (cron time, idempotency) is verified against the real
APScheduler and the job body is invoked directly (no waiting for 04:00).
"""

from __future__ import annotations

import logging

import pytest

from core.scheduler import Scheduler
from features.minecraft.scheduler import BACKUP_JOB_ID, MinecraftBackupScheduler


def test_backup_job_registered_daily_0400() -> None:
    scheduler = Scheduler(timezone="Asia/Seoul")
    backup = MinecraftBackupScheduler(scheduler, backup_service=object())
    backup.register()

    job = scheduler.backend.get_job(BACKUP_JOB_ID)
    assert job is not None
    assert "hour='4'" in repr(job.trigger)
    assert "minute='0'" in repr(job.trigger)
    assert str(job.trigger.timezone) == "Asia/Seoul"


def test_backup_job_register_is_idempotent() -> None:
    scheduler = Scheduler()
    backup = MinecraftBackupScheduler(scheduler, backup_service=object())
    backup.register()
    backup.register()

    jobs = [job for job in scheduler.backend.get_jobs() if job.id == BACKUP_JOB_ID]
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_backup_job_run_success(caplog: pytest.LogCaptureFixture) -> None:
    class FakeBackup:
        async def backup_all(self) -> tuple[int, int]:
            return (2, 0)

        async def cleanup_all(self) -> dict[str, list]:
            return {}

    scheduler = Scheduler()
    backup = MinecraftBackupScheduler(scheduler, FakeBackup())

    with caplog.at_level(logging.INFO, logger="features.minecraft.scheduler"):
        await backup._run()

    messages = [record.getMessage() for record in caplog.records]
    assert any("Minecraft Backup Started" in message for message in messages)
    assert any("Minecraft Backup Finished" in message for message in messages)


@pytest.mark.asyncio
async def test_backup_job_handles_failure(caplog: pytest.LogCaptureFixture) -> None:
    class FailingBackup:
        async def backup_all(self) -> tuple[int, int]:
            raise RuntimeError("boom")

        async def cleanup_all(self) -> dict[str, list]:
            return {}

    scheduler = Scheduler()
    backup = MinecraftBackupScheduler(scheduler, FailingBackup())

    with caplog.at_level(logging.ERROR, logger="features.minecraft.scheduler"):
        await backup._run()  # must not raise / crash the bot

    messages = [record.getMessage() for record in caplog.records]
    assert any("Minecraft Backup Failed" in message for message in messages)
