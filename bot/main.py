"""Plaything v2 bot — process entry point.

Responsible for: config loading, DB pool creation, migration, Discord
login, cog loading, and wiring the log-to-Discord consumer and scheduler.
"""

from __future__ import annotations

import asyncio

from config.settings import BOT_PREFIX, get_settings
from core.container import init_container
from core.discord import create_bot, load_cogs
from core.logger import async_logger_consumer, get_logger, setup_logging
from core.scheduler import scheduler_heartbeat

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
    from alembic import command
    from alembic.config import Config

    settings = container.settings
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_dsn)
    command.upgrade(alembic_cfg, "head")
    log.info("migrations applied")


async def _start_log_consumer(bot, container, message_queue) -> None:
    """Start the task that forwards queued logs to the Discord channel.

    Args:
        bot: The bot instance.
        container: The dependency container.
        message_queue: The queue fed by the logging handlers.
    """

    async def post(message: str) -> None:
        channel = bot.get_channel(container.settings.log_channel_id)
        if channel is None:
            return
        for part in _chunk(message):
            await channel.send(part)

    return async_logger_consumer(message_queue, post)


def _chunk(text: str, limit: int = 1900) -> list[str]:
    """Split a message into Discord-safe chunks on whitespace boundaries.

    Args:
        text: The message text.
        limit: Maximum chunk length.

    Returns:
        A list of chunk strings.
    """
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    current = ""
    for word in text.split(" "):
        if len(current) + len(word) + 1 > limit:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        parts.append(current)
    return parts


async def _register_jobs(container) -> None:
    """Register periodic background jobs.

    Args:
        container: The dependency container exposing the scheduler.
    """
    container.scheduler.add_job(scheduler_heartbeat, "interval", minutes=1, id="heartbeat")
    log.info("scheduled jobs registered")


async def _run(container, log_queue: asyncio.Queue[str]) -> None:
    """Core async startup sequence.

    Args:
        container: The configured dependency container.
        log_queue: The queue fed by logging handlers for Discord output.
    """
    await container.database.connect()
    await _run_migrations(container)

    bot = create_bot(BOT_PREFIX, sync_commands=True)
    container.bind_bot(bot)
    bot.database = container.database
    bot.container = container

    log_task = await _start_log_consumer(bot, container, log_queue)

    await load_cogs(bot, COGS)

    await _register_jobs(container)
    await container.scheduler.start()

    try:
        async with bot:
            await bot.start(container.settings.discord_token)
    finally:
        await container.scheduler.shutdown()
        log_task.cancel()
        await container.minecraft_service.shutdown_all()
        await container.database.close()


def main() -> None:
    """Entry point with clear failure reporting for each startup step."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    setup_logging(queue)

    try:
        settings = get_settings()
    except Exception as exc:  # config validation failure
        log.critical("Configuration error: %s", exc, exc_info=exc)
        raise SystemExit(1) from exc

    container = init_container(settings)
    container.log_queue = queue

    try:
        asyncio.run(_run(container, queue))
    except SystemExit:
        raise
    except Exception as exc:
        log.critical("Fatal error during startup/shutdown: %s", exc, exc_info=exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
