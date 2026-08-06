"""Plaything v2 bot — process entry point.

Responsible for: config loading, DB pool creation, migration, Discord
login, cog loading, and wiring the log-to-Discord worker and scheduler.

Lifecycle contract:
    Bot Started → Bot Ready → Scheduler Start
    Scheduler Shutdown → Bot Close → DB Close
"""

from __future__ import annotations

import asyncio

from config.settings import BOT_PREFIX, get_settings
from core.container import init_container
from core.discord import create_bot, load_cogs
from core.discord_handler import DiscordLogWorker
from core.logger import get_logger, log_event, setup_logging
from core.scheduler import scheduler_heartbeat

# NOTE: The meal feature is normally driven solely by the daily scheduler job
# (07:00 Asia/Seoul). The meal cog provides debug/integration-test commands
# (``/급식``, ``/급식날짜``) so operators can trigger the pipeline on demand.
COGS = [
    "features.help.commands",
    "features.minecraft.commands",
    "features.music.commands",
    "features.tts.commands",
    "features.meal.commands",
]

log = get_logger(__name__)


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


async def _register_jobs(container) -> None:
    """Register periodic background jobs.

    Args:
        container: The dependency container exposing the scheduler.
    """
    container.scheduler.add_job(scheduler_heartbeat, "interval", minutes=1, id="heartbeat")
    container.meal_scheduler.register()
    log.info("scheduled jobs registered")


async def _run(container, log_queue: asyncio.Queue) -> None:
    """Core async startup sequence.

    Args:
        container: The configured dependency container.
        log_queue: The queue fed by logging handlers for Discord output.
    """
    await container.database.connect()
    await _run_migrations(container)

    bot = create_bot(
        BOT_PREFIX,
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
        # Lifecycle: stop background jobs first, then close the bot. The
        # shutdown event is logged before the log worker drains and stops.
        await container.scheduler.shutdown()
        await container.minecraft_service.shutdown_all()
        await container.meal_service.close()
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


if __name__ == "__main__":
    main()
