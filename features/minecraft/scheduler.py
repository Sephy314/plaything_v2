"""Daily Minecraft backup scheduler.

Registers a cron job on the shared :class:`Scheduler` that, at the configured
time, backs up every registered server and prunes expired backups (retention).
Per-server failures are caught and logged so one failing server never stops the
others.
"""

from __future__ import annotations

import logging

from core.logger import get_logger, log_event

log = get_logger(__name__)

BACKUP_JOB_ID = "minecraft_daily_backup"
BACKUP_TIMEZONE = "Asia/Seoul"
BACKUP_HOUR = 4
BACKUP_MINUTE = 0


class MinecraftBackupScheduler:
    """Owns the daily backup job registration and execution.

    Args:
        scheduler: The shared :class:`Scheduler` instance.
        backup_service: The :class:`MinecraftBackupService`.
        timezone: IANA timezone for the cron trigger (default Asia/Seoul).
        hour: Hour of day (24h) for the daily backup.
        minute: Minute of hour for the daily backup.
    """

    def __init__(
        self,
        scheduler,
        backup_service,
        *,
        timezone: str = BACKUP_TIMEZONE,
        hour: int = BACKUP_HOUR,
        minute: int = BACKUP_MINUTE,
    ) -> None:
        self._scheduler = scheduler
        self._backup_service = backup_service
        self._timezone = timezone
        self._hour = hour
        self._minute = minute

    def register(self) -> None:
        """Register the daily backup job (idempotent)."""
        if self._scheduler.backend.get_job(BACKUP_JOB_ID) is not None:
            return
        self._scheduler.add_job(
            self._run,
            "cron",
            id=BACKUP_JOB_ID,
            hour=self._hour,
            minute=self._minute,
            timezone=self._timezone,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info(
            "registered minecraft backup job: daily %02d:%02d %s",
            self._hour,
            self._minute,
            self._timezone,
        )

    async def _run(self) -> None:
        """Scheduler entry point: back up every server, then prune old backups."""
        log_event(log, "Minecraft Backup Started", timezone=self._timezone)
        try:
            total, failures = await self._backup_service.backup_all()
        except Exception as exc:
            log_event(
                log,
                "Minecraft Backup Failed",
                level=logging.ERROR,
                error=str(exc),
            )
            log.error("automatic backup failed: %s", exc, exc_info=exc)
            return

        try:
            await self._backup_service.cleanup_all()
        except Exception as exc:
            log.error(
                "automatic backup retention cleanup failed: %s", exc, exc_info=exc
            )

        log_event(
            log,
            "Minecraft Backup Finished",
            details={"servers": total, "failures": failures},
        )
