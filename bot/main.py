"""Plaything v2 bot — process entry point.

Responsible for: config loading, DB pool creation, migration, Discord
login, cog loading, and wiring the log-to-Discord worker and scheduler.

Lifecycle contract:
    Bot Started → Bot Ready → Scheduler Start
    Scheduler Shutdown → Bot Close → DB Close
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import commands

from config.settings import get_settings
from core.container import init_container
from core.discord import create_bot, load_cogs
from core.discord_handler import DiscordLogWorker
from core.exceptions import ConfigurationException
from core.logger import get_logger, log_event, setup_logging
from core.scheduler import scheduler_heartbeat
from services.system_service import RESTART_EXIT_CODE

# NOTE: The meal feature is normally driven solely by the daily scheduler job
# (07:00 Asia/Seoul). The meal cog provides debug/integration-test commands
# (``/급식``, ``/급식날짜``) so operators can trigger the pipeline on demand.
COGS = [
    "features.help.commands",
    "features.minecraft.commands",
    "features.music.commands",
    "features.tts.commands",
    "features.meal.commands",
    "features.admin.commands",
]

log = get_logger(__name__)


def validate_config(settings) -> None:
    """Fail fast with a clear message when required settings are missing.

    ``DATABASE_DSN`` and ``DISCORD_TOKEN`` are already required and validated
    by :class:`Settings`; ``LOG_CHANNEL_ID`` is validated here so operational
    logging is guaranteed before the bot starts.

    Raises:
        ConfigurationException: If a required setting is missing.
    """
    if not settings.log_channel_id:
        raise ConfigurationException(
            "LOG_CHANNEL_ID is required for operational logging. "
            "Set it to the Discord channel where log embeds should be sent "
            "and restart the bot."
        )


def _handle_loop_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Global asyncio exception handler.

    Logs otherwise-unhandled loop exceptions to every destination (console /
    file / Discord) and does not take down the process — discord.py's own
    reconnect logic keeps the bot alive.
    """
    message = context.get("message", "Unhandled exception in event loop")
    exc = context.get("exception")
    log.critical("unhandled loop exception: %s", message, exc_info=exc)
    log_event(
        log,
        "Unhandled Loop Exception",
        level=logging.ERROR,
        error=message,
    )


async def _run_migrations(container) -> None:
    """Run Alembic migrations synchronously from the composed app.

    Args:
        container: The dependency container exposing the settings.
    """
    import logging

    from alembic import command
    from alembic.config import Config

    settings = container.settings
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_dsn)

    # Alembic's env.py calls ``logging.config.fileConfig`` which replaces the
    # root handler set and, with the default settings, disables existing
    # loggers. Snapshot and restore the app's handlers so structured logging
    # (console / file / Discord) survives the migration run.
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        root.handlers = saved_handlers
        root.level = saved_level
    log.info("migrations applied")


def _start_log_worker(bot, container, queue) -> DiscordLogWorker:
    """Start the worker that posts queued log events to the log channel.

    Args:
        bot: The bot instance (used to resolve the log channel).
        container: The dependency container exposing the settings.
        queue: The queue fed by the logging handlers.

    Returns:
        A running :class:`DiscordLogWorker`.
    """
    worker = DiscordLogWorker(
        queue,
        channel_provider=lambda: bot.get_channel(container.settings.log_channel_id),
    )
    worker.start()
    return worker


async def _start_scheduler_on_ready(container) -> None:
    """Start the scheduler once the bot is ready (per lifecycle contract).

    Args:
        container: The dependency container exposing the scheduler.
    """
    await container.scheduler.start()
    # Resolve and log the meal target channel at startup (INFO with id, or
    # ERROR when unresolvable) so a misconfigured channel is visible
    # immediately instead of silently dropping the daily meal.
    await container.resolve_meal_channel()


async def _register_jobs(container) -> None:
    """Register periodic background jobs.

    Args:
        container: The dependency container exposing the scheduler.
    """
    container.scheduler.add_job(scheduler_heartbeat, "interval", minutes=1, id="heartbeat")
    container.meal_scheduler.register()
    container.minecraft_backup_scheduler.register()
    log.info("scheduled jobs registered")


async def _run(container, log_queue: asyncio.Queue) -> None:
    """Core async startup sequence.

    Args:
        container: The configured dependency container.
        log_queue: The queue fed by logging handlers for Discord output.
    """
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_handle_loop_exception)

    await container.database.connect()
    await _run_migrations(container)

    bot = create_bot(
        # Slash commands are the only command surface. ``!`` prefix parsing is
        # disabled entirely — prefix commands are not registered anywhere.
        commands.when_mentioned,
        sync_commands=True,
        on_ready=lambda: _start_scheduler_on_ready(container),
    )
    container.bind_bot(bot)
    bot.database = container.database
    bot.container = container

    log_worker = _start_log_worker(bot, container, log_queue)

    await load_cogs(bot, COGS)

    await _register_jobs(container)

    try:
        await bot.start(container.settings.discord_token)
    finally:
        # Graceful shutdown: scheduler → background tasks → voice → Minecraft
        # → meal → Discord logout → DB. Any exception during cleanup is logged
        # but never allowed to mask the original exit path.
        try:
            await container.scheduler.shutdown()
        except Exception as exc:
            log.error("scheduler shutdown failed: %s", exc, exc_info=exc)
        try:
            await container.task_manager.shutdown()
        except Exception as exc:
            log.error("task manager shutdown failed: %s", exc, exc_info=exc)
        if container.voice_manager is not None:
            try:
                await container.voice_manager.shutdown_all()
            except Exception as exc:
                log.error("voice shutdown failed: %s", exc, exc_info=exc)
        try:
            await container.minecraft_service.shutdown_all()
        except Exception as exc:
            log.error("minecraft shutdown failed: %s", exc, exc_info=exc)
        try:
            await container.meal_service.close()
        except Exception as exc:
            log.error("meal service close failed: %s", exc, exc_info=exc)
        log_event(log, "Bot Shutdown")
        await log_worker.stop()
        await bot.close()
        await container.database.close()


def main() -> None:
    """Entry point with clear failure reporting for each startup step."""
    # Bounded queue: a log burst drops records instead of growing unbounded.
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    setup_logging(queue)

    try:
        settings = get_settings()
    except Exception as exc:  # config validation failure
        log.critical("Configuration error: %s", exc, exc_info=exc)
        raise SystemExit(1) from exc

    try:
        validate_config(settings)
    except ConfigurationException as exc:
        log.critical("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    container = init_container(settings)
    container.log_queue = queue
    log_event(log, "Bot Started")

    try:
        asyncio.run(_run(container, queue))
    except SystemExit:
        raise
    except Exception as exc:
        log.critical("Fatal error during startup/shutdown: %s", exc, exc_info=exc)
        raise SystemExit(1) from exc

    # An admin lifecycle command (shutdown/restart) sets the requested exit
    # code before closing the bot. Restart codes let the container restart
    # policy (or systemd) re-spawn the process.
    if container.shutdown_exit_code is not None:
        code = container.shutdown_exit_code
        if code == RESTART_EXIT_CODE:
            log.info("process exiting with restart code %s", code)
        raise SystemExit(code)


if __name__ == "__main__":
    main()
