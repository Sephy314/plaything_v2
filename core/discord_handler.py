"""Discord logging handler and asynchronous worker.

The logging pipeline never blocks the event loop: records are converted into
structured :class:`LogEvent` items and pushed onto an ``asyncio`` queue. A
single background :class:`DiscordLogWorker` drains the queue, renders each
event as a Discord embed and posts them to the configured log channel.

Design properties:

* **Bounded queue** — the handler drops records when the queue is full so a
  log burst cannot exhaust memory.
* **Rate-limit friendly** — events are batched into messages (up to 10 embeds
  per message) and a minimum interval is kept between consecutive sends.
* **Ordering** — a FIFO queue plus a single sequential worker guarantees
  events are delivered in the order they were logged.
* **Crash-proof** — channel missing/deleted, missing permissions or API
  timeouts are caught and logged to console/file only; the bot never dies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

#: Timezone used for human-readable log timestamps (Asia/Seoul).
LOG_TIMEZONE = "Asia/Seoul"
#: Maximum number of embeds Discord accepts inside a single message.
MAX_EMBEDS_PER_MESSAGE = 10

#: Embed side-bar color per logging level.
LEVEL_COLORS: dict[int, discord.Color] = {
    logging.DEBUG: discord.Color(0x9E9E9E),
    logging.INFO: discord.Color(0x4FC3F7),
    logging.WARNING: discord.Color(0xFFB74D),
    logging.ERROR: discord.Color(0xE57373),
    logging.CRITICAL: discord.Color(0xB71C1C),
}

_worker_busy = False


def _set_worker_busy(value: bool) -> None:
    """Set whether the Discord worker is currently sending."""
    global _worker_busy  # noqa: PLW0603
    _worker_busy = value


def _is_worker_busy() -> bool:
    """Return whether the Discord worker is currently sending."""
    return _worker_busy


@dataclass(frozen=True)
class LogEvent:
    """Structured log record destined for a Discord embed.

    Attributes:
        level: Numeric ``logging`` level.
        level_name: Uppercase level name, e.g. ``"INFO"``.
        timestamp: UTC-aware timestamp of the record.
        logger_name: Logger that produced the record.
        message: Fully formatted log message.
        event: Optional human-readable event name used as the embed title.
        details: Optional key/value map rendered in a "Details" embed field.
    """

    level: int
    level_name: str
    timestamp: datetime
    logger_name: str
    message: str
    event: str | None = None
    details: dict[str, Any] | None = None


class DiscordLogHandler(logging.Handler):
    """Publish log records to an ``asyncio`` queue as :class:`LogEvent` items.

    ``emit`` is synchronous (as required by :class:`logging.Handler`) but never
    blocks: it formats the record and enqueues it with ``put_nowait``. When the
    bounded queue is full the record is dropped to protect the process and
    Discord rate limits.
    """

    def __init__(self, queue: asyncio.Queue[LogEvent]) -> None:
        super().__init__(level=logging.INFO)
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if _is_worker_busy():
                # Logging triggered from inside the Discord worker (e.g. a
                # failed send) must not be enqueued or it creates a loop.
                return
            event = LogEvent(
                level=record.levelno,
                level_name=record.levelname,
                timestamp=datetime.fromtimestamp(record.created, tz=UTC),
                logger_name=record.name,
                message=self.format(record),
                event=getattr(record, "log_event", None),
                details=getattr(record, "log_details", None),
            )
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Log burst — drop the record instead of blocking or growing.
            return
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)


def build_log_embed(event: LogEvent) -> discord.Embed:
    """Convert a :class:`LogEvent` into a Discord embed.

    Args:
        event: The structured log event to render.

    Returns:
        A ready-to-send :class:`discord.Embed`.
    """
    first_line = event.message.splitlines()[0] if event.message else "Log"
    title = (event.event or first_line).strip()[:256] or "Log"

    embed = discord.Embed(
        title=title,
        color=LEVEL_COLORS.get(event.level, discord.Color.default()),
        timestamp=event.timestamp,
    )
    if event.event is None and len(event.message) > len(title):
        embed.description = event.message[:4096]

    embed.add_field(name="Level", value=event.level_name, inline=True)
    embed.add_field(name="Time", value=_format_kst(event.timestamp), inline=True)
    if event.logger_name:
        embed.set_footer(text=event.logger_name)
    if event.details:
        details = "\n".join(f"{key}={value}" for key, value in event.details.items())
        embed.add_field(name="Details", value=details[:1024], inline=False)
    return embed


def _format_kst(value: datetime) -> str:
    """Format a datetime in the bot's primary timezone (Asia/Seoul)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(LOG_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")


class DiscordLogWorker:
    """Drain the log queue and post embeds to the log channel.

    Args:
        queue: Bounded queue fed by :class:`DiscordLogHandler`.
        channel_provider: Zero-arg callable returning the target channel or
            ``None`` (e.g. ``lambda: bot.get_channel(channel_id)``).
        batch_max: Maximum events collected per drain cycle.
        max_embeds: Maximum embeds per message (Discord caps at 10).
        send_interval: Minimum seconds between consecutive messages.
    """

    def __init__(
        self,
        queue: asyncio.Queue[LogEvent],
        channel_provider: Callable[[], Any],
        *,
        batch_max: int = 5,
        max_embeds: int = MAX_EMBEDS_PER_MESSAGE,
        send_interval: float = 1.0,
    ) -> None:
        self._queue = queue
        self._channel_provider = channel_provider
        self._batch_max = max(1, batch_max)
        self._max_embeds = min(max(1, max_embeds), MAX_EMBEDS_PER_MESSAGE)
        self._send_interval = max(0.0, send_interval)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background consumer task (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="discord-log-worker")

    async def stop(self) -> None:
        """Stop and await the consumer task (safe to call twice).

        Pending queued events are drained (with a short timeout) before the
        task is cancelled so shutdown events like ``"Bot Shutdown"`` still make
        it to the log channel.
        """
        if self._task is None:
            return
        task, self._task = self._task, None
        try:
            await asyncio.wait_for(self._queue.join(), timeout=2)
        except TimeoutError:
            pass
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                first = await self._queue.get()
            except asyncio.CancelledError:
                return
            batch = self._collect(first)
            await self._send(batch)
            for _ in batch:
                self._queue.task_done()

    def _collect(self, first: LogEvent) -> list[LogEvent]:
        """Drain as many queued events as fit into the current batch."""
        batch = [first]
        while len(batch) < self._batch_max:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _send(self, events: list[LogEvent]) -> None:
        """Send a batch of events to the log channel (never raises)."""
        _set_worker_busy(True)
        try:
            channel = self._channel_provider()
            if channel is None:
                logging.getLogger(__name__).debug(
                    "log channel not available; dropped %d event(s)", len(events)
                )
                return
            embeds = [build_log_embed(event) for event in events]
            for index in range(0, len(embeds), self._max_embeds):
                chunk = embeds[index : index + self._max_embeds]
                await channel.send(embeds=chunk)
                if index + self._max_embeds < len(embeds):
                    await asyncio.sleep(self._send_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logging.getLogger(__name__).warning("failed to send Discord log embed: %s", exc)
        finally:
            _set_worker_busy(False)
