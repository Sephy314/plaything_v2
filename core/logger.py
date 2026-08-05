"""Centralized logging infrastructure.

Emits logs to three destinations:

* Console
* Rotating file
* Discord channel (asynchronously, via a queue drained by the bot)

The logger is exposed as a singleton through :func:`get_logger`.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from logging import Logger
from logging.handlers import RotatingFileHandler

import structlog

from config.settings import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured = False


class DiscordLogHandler(logging.Handler):
    """Forward log records to an asyncio queue for external consumption.

    The consumer is provided at construction time and is responsible for
    posting messages to a Discord channel.
    """

    def __init__(self, queue: asyncio.Queue[str]) -> None:
        super().__init__(level=logging.INFO)
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._queue.put_nowait(message)
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)


def _build_handlers(queue: asyncio.Queue[str] | None) -> list[logging.Handler]:
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
        discord_handler = DiscordLogHandler(queue)
        discord_handler.setLevel(logging.INFO)
        discord_handler.setFormatter(formatter)
        handlers.append(discord_handler)

    return handlers


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


def async_logger_consumer(
    queue: asyncio.Queue[str],
    post: callable,
) -> asyncio.Task:
    """Create a background task that drains log messages and posts them.

    Args:
        queue: Queue of formatted log messages.
        post: Coroutine callback accepting a single string message.

    Returns:
        An asyncio task draining the queue.
    """

    async def _drain() -> None:
        while True:
            message = await queue.get()
            try:
                await post(message)
            finally:
                queue.task_done()

    return asyncio.create_task(_drain(), name="discord-log-consumer")
