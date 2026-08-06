"""Centralized logging infrastructure.

Emits logs to three destinations:

* Console
* Rotating file
* Discord channel (asynchronously, via a queue drained by the bot)

Structured operational events (e.g. ``"Bot Ready"``, ``"Meal Fetch Success"``)
can be emitted through :func:`log_event`; they are rendered as rich embeds in
the Discord log channel.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from logging import Logger
from logging.handlers import RotatingFileHandler

import structlog

from config.settings import PROJECT_ROOT
from core.discord_handler import DiscordLogHandler

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured = False


def _build_handlers(queue: asyncio.Queue | None) -> list[logging.Handler]:
    """Construct the stream/file/discord handlers for a logger."""
    formatter = logging.Formatter(LOG_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.DEBUG)
    stream.setFormatter(formatter)

    handlers: list[logging.Handler] = [stream]

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    if queue is not None:
        # The Discord handler renders the raw message into an embed itself, so
        # it intentionally keeps the default formatter (message only).
        discord_handler = DiscordLogHandler(queue)
        discord_handler.setLevel(logging.INFO)
        handlers.append(discord_handler)

    journal_handler = _build_journal_handler()
    if journal_handler is not None:
        journal_handler.setLevel(logging.INFO)
        journal_handler.setFormatter(formatter)
        handlers.append(journal_handler)

    return handlers


def _build_journal_handler() -> logging.Handler | None:
    """Return a systemd journal handler, or ``None`` if unavailable.

    The ``systemd`` python module is optional; when it is missing (e.g. a
    Docker image without the package) the handler is skipped gracefully and
    the remaining stream/file/discord destinations still apply.
    """
    try:
        from systemd.journal import JournalHandler

        return JournalHandler()
    except Exception:
        return None


def setup_logging(queue: asyncio.Queue[str] | None = None) -> None:
    """Configure the process-wide logger. Idempotent.

    Args:
        queue: Optional asyncio queue for Discord-bound log messages.
    """
    global _configured
    if _configured:
        return

    basic = logging.getLogger()
    basic.setLevel(logging.DEBUG)
    for handler in _build_handlers(queue):
        basic.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "plaything") -> Logger:
    """Return a stdlib logger bound to the given module name.

    Args:
        name: Logger name; should usually be ``__name__``.

    Returns:
        The configured stdlib logger.
    """
    return logging.getLogger(name)


def log_event(
    logger: Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **details: object,
) -> None:
    """Emit a structured operational event (rendered as a Discord embed).

    The event name becomes the embed title and ``details`` are shown in the
    embed "Details" field. The record is also written to console/file through
    the normal handlers.

    Args:
        logger: Logger to emit through (all destinations).
        event: Human-readable event name, e.g. ``"Minecraft Server Started"``.
        level: Logging level for the event.
        **details: Key/value pairs shown in the embed "Details" field.
    """
    logger.log(
        level,
        event,
        extra={"log_event": event, "log_details": details or None},
    )
