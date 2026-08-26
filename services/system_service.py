"""System service — health check, graceful shutdown and restart.

Provides the operations-facing API used by the admin cog:

* :meth:`health` — a snapshot of bot / database / scheduler / voice state.
* :meth:`shutdown` — request a graceful shutdown (closes the bot; the
  startup ``finally`` block drains scheduler, voice, Minecraft, DB).
* :meth:`restart` — same graceful close, then exit with a restart code so the
  container restart policy (or systemd) brings the process back up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING, Any

from core.logger import get_logger, log_event

if TYPE_CHECKING:
    from core.container import Container

log = get_logger(__name__)

#: Exit code used to signal "restart the process" to the supervisor.
RESTART_EXIT_CODE = 42


def _app_version() -> str:
    """Return the installed package version, or ``unknown``."""
    try:
        return metadata.version("plaything-v2")
    except metadata.PackageNotFoundError:
        return "unknown"


class SystemService:
    """Orchestrates health checks and process lifecycle for the bot."""

    def __init__(self, container: Container) -> None:
        self._container = container

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Return a snapshot of the bot's runtime health.

        Returns:
            A dict with bot / ping / database / scheduler / voice / uptime
            fields suitable for rendering in the status command.
        """
        bot = self._container.bot
        voice = self._container.voice_manager
        uptime = datetime.now(UTC) - self._container.started_at

        return {
            "bot_online": bool(bot and bot.is_ready()),
            "ping_ms": round(bot.latency * 1000, 1) if bot and bot.is_ready() else 0.0,
            "database": "ok" if await self._container.database.ping() else "error",
            "scheduler": ("running" if self._container.scheduler.backend.running else "stopped"),
            "voice_connections": voice.connection_count if voice else 0,
            "uptime_seconds": int(uptime.total_seconds()),
            "version": _app_version(),
        }

    # ------------------------------------------------------------------
    # Channel status
    # ------------------------------------------------------------------

    async def log_channel_status(self) -> dict[str, Any]:
        """Resolve the configured log channel and report its status.

        Returns:
            A dict describing whether the log channel is configured and
            resolvable (cache, then REST).
        """
        channel_id = self._container.settings.log_channel_id
        channel = await self._resolve_channel(channel_id)
        report = self._channel_report(channel_id, channel)
        report["log_channel_id"] = channel_id
        return report

    async def meal_channel_status(self) -> dict[str, Any]:
        """Re-resolve the meal target channel and report its status.

        Re-runs the startup resolution (cache, then REST) so the report
        reflects the current Discord state rather than a stale cached value.

        Returns:
            A dict describing the effective meal channel plus the configured
            ``MEAL_CHANNEL_ID`` / ``LOG_CHANNEL_ID`` values.
        """
        settings = self._container.settings
        await self._container.resolve_meal_channel()
        effective_id = settings.meal_channel_id or settings.log_channel_id
        report = self._channel_report(effective_id, self._container._meal_channel_provider())
        report.update(
            {
                "meal_channel_id": settings.meal_channel_id,
                "log_channel_id": settings.log_channel_id,
            }
        )
        return report

    async def _resolve_channel(self, channel_id: int) -> Any:
        """Resolve a channel id from the bot cache, then the REST API."""
        if not channel_id:
            return None
        bot = self._container.bot
        if bot is None:
            return None
        channel = bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await bot.fetch_channel(channel_id)
        except Exception:
            return None

    @staticmethod
    def _channel_report(channel_id: int, channel: Any) -> dict[str, Any]:
        """Build a serialisable channel-status report."""
        return {
            "channel_id": channel_id,
            "configured": bool(channel_id),
            "found": channel is not None,
            "name": getattr(channel, "name", None),
            "mention": getattr(channel, "mention", None),
            "type": type(channel).__name__ if channel is not None else None,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self, reason: str) -> None:
        """Gracefully stop the bot.

        Sets the process exit code to ``0`` and closes the Discord bot. The
        startup cleanup path (scheduler → voice → Minecraft → DB) runs in the
        ``finally`` block of the entry point.

        Args:
            reason: Human-readable reason for the shutdown (logging).
        """
        log_event(log, "Bot Shutdown Requested", reason=reason)
        self._container.shutdown_exit_code = 0
        await self._close_bot()

    async def restart(self, reason: str) -> None:
        """Gracefully restart the bot.

        Runs the same cleanup as :meth:`shutdown` but exits with
        :data:`RESTART_EXIT_CODE` so the container restart policy (or a
        supervisor) brings the process back.

        Args:
            reason: Human-readable reason for the restart (logging).
        """
        log_event(log, "Bot Restart Requested", reason=reason)
        self._container.shutdown_exit_code = RESTART_EXIT_CODE
        await self._close_bot()

    async def _close_bot(self) -> None:
        """Close the Discord client so ``bot.start()`` returns."""
        bot = self._container.bot
        if bot is not None:
            try:
                await bot.close()
            except Exception as exc:  # pragma: no cover - defensive
                log.error("failed to close bot cleanly: %s", exc, exc_info=exc)
