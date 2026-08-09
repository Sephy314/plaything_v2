"""Tests for the Discord logging pipeline.

Covers: console/file/Discord handler wiring, handler→queue behaviour, embed
rendering, the async worker (batching + rate-limit-safe sends), and the
:func:`core.logger.log_event` helper.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from unittest.mock import AsyncMock

import pytest

from core.discord_handler import (
    DiscordLogHandler,
    DiscordLogWorker,
    LogEvent,
    build_log_embed,
)
from core.logger import log_event


def _make_record(
    msg: str = "hello %s",
    args: tuple = ("world",),
    level: int = logging.INFO,
    extra: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


def _make_event(message: str = "hello") -> LogEvent:
    return LogEvent(
        level=logging.INFO,
        level_name="INFO",
        timestamp=datetime.now(UTC),
        logger_name="test.logger",
        message=message,
    )


# ----------------------------------------------------------------------
# Handler → queue
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_emits_log_event_to_queue() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue()
    handler = DiscordLogHandler(queue)
    handler.emit(_make_record())

    event = queue.get_nowait()
    assert event.level == logging.INFO
    assert event.level_name == "INFO"
    assert event.message == "hello world"
    assert event.logger_name == "test.logger"
    assert event.event is None


@pytest.mark.asyncio
async def test_handler_carries_event_metadata() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue()
    handler = DiscordLogHandler(queue)
    handler.emit(
        _make_record(
            msg="Minecraft Server Started",
            args=(),
            level=logging.WARNING,
            extra={"log_event": "Minecraft Server Started", "log_details": {"server": "survival"}},
        )
    )

    event = queue.get_nowait()
    assert event.event == "Minecraft Server Started"
    assert event.details == {"server": "survival"}
    assert event.level_name == "WARNING"


@pytest.mark.asyncio
async def test_handler_drops_when_queue_full() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=1)
    handler = DiscordLogHandler(queue)
    handler.emit(_make_record(msg="first", args=()))
    handler.emit(_make_record(msg="second", args=()))  # must not raise

    assert queue.qsize() == 1


# ----------------------------------------------------------------------
# Embed rendering
# ----------------------------------------------------------------------


def test_build_log_embed() -> None:
    event = LogEvent(
        level=logging.ERROR,
        level_name="ERROR",
        timestamp=datetime(2026, 8, 5, 22, 0, tzinfo=UTC),
        logger_name="features.minecraft",
        message="Minecraft Server Started",
        event="Minecraft Server Started",
        details={"server": "survival"},
    )

    embed = build_log_embed(event)
    assert embed.title == "Minecraft Server Started"
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Level"] == "ERROR"
    # 22:00 UTC == 07:00 next day in Asia/Seoul
    assert fields["Time"] == "2026-08-06 07:00:00"
    assert fields["Details"] == "server=survival"
    assert embed.footer.text == "features.minecraft"


# ----------------------------------------------------------------------
# Async worker
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_sends_embeds_to_channel() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue()
    channel = AsyncMock()
    worker = DiscordLogWorker(queue, channel_provider=lambda: channel)
    worker.start()
    try:
        queue.put_nowait(_make_event("hello"))
        await asyncio.wait_for(queue.join(), timeout=2)
        channel.send.assert_awaited_once()
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_stop_drains_pending_events() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue()
    channel = AsyncMock()
    worker = DiscordLogWorker(queue, channel_provider=lambda: channel)
    worker.start()
    # Both events must be sent before the worker stops (no lost shutdown logs).
    queue.put_nowait(_make_event("one"))
    queue.put_nowait(_make_event("two"))
    await worker.stop()

    assert channel.send.await_count == 1
    sent_embeds = channel.send.await_args.kwargs["embeds"]
    assert {embed.title for embed in sent_embeds} == {"one", "two"}


@pytest.mark.asyncio
async def test_worker_drops_when_channel_missing() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue()
    worker = DiscordLogWorker(queue, channel_provider=lambda: None)
    worker.start()
    try:
        queue.put_nowait(_make_event("hello"))
        await asyncio.wait_for(queue.join(), timeout=2)
        # Worker must survive a missing channel and keep running.
        assert worker._task is not None and not worker._task.done()
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_survives_send_error() -> None:
    queue: asyncio.Queue[LogEvent] = asyncio.Queue()

    class BrokenChannel:
        async def send(self, **kwargs) -> None:
            raise RuntimeError("no permission")

    worker = DiscordLogWorker(queue, channel_provider=lambda: BrokenChannel())
    worker.start()
    try:
        queue.put_nowait(_make_event("hello"))
        await asyncio.wait_for(queue.join(), timeout=2)
        assert worker._task is not None and not worker._task.done()
    finally:
        await worker.stop()


# ----------------------------------------------------------------------
# log_event helper
# ----------------------------------------------------------------------


def test_log_event_helper_sets_metadata(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.log_event")
    with caplog.at_level(logging.WARNING, logger="test.log_event"):
        log_event(
            logger,
            "Meal Fetch Failed",
            level=logging.WARNING,
            date="20260806",
            error="boom",
        )

    records = [record for record in caplog.records if record.name == "test.log_event"]
    assert len(records) == 1
    record = records[0]
    assert record.log_event == "Meal Fetch Failed"
    assert record.log_details == {"date": "20260806", "error": "boom"}
    assert record.levelno == logging.WARNING


# ----------------------------------------------------------------------
# setup_logging wiring (console / file / discord)
# ----------------------------------------------------------------------


def test_setup_logging_installs_expected_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import logger as logger_mod

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    monkeypatch.setattr(logger_mod, "_configured", False)

    queue: asyncio.Queue = asyncio.Queue()
    logger_mod.setup_logging(queue)
    try:
        handlers = root.handlers
        assert any(isinstance(h, logging.StreamHandler) for h in handlers)
        assert any(isinstance(h, RotatingFileHandler) for h in handlers)
        assert any(isinstance(h, DiscordLogHandler) for h in handlers)
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
        logger_mod._configured = False
