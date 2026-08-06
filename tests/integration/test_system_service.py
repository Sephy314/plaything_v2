"""Integration tests for the system service (health / shutdown / restart)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.system_service import RESTART_EXIT_CODE, SystemService


class FakeBot:
    def __init__(self, ready: bool = True, latency: float = 0.12) -> None:
        self._ready = ready
        self.latency = latency
        self.closed = False

    def is_ready(self) -> bool:
        return self._ready

    async def close(self) -> None:
        self.closed = True


class FakeDatabase:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    async def ping(self) -> bool:
        return self._ok


class FakeScheduler:
    def __init__(self, running: bool = True) -> None:
        self.backend = SimpleNamespace(running=running)


class FakeVoiceManager:
    connection_count = 2


class FakeContainer:
    def __init__(
        self,
        *,
        db_ok: bool = True,
        scheduler_running: bool = True,
        bot_ready: bool = True,
    ) -> None:
        self.database = FakeDatabase(db_ok)
        self.scheduler = FakeScheduler(scheduler_running)
        self.voice_manager = FakeVoiceManager()
        self.bot = FakeBot(bot_ready)
        self.started_at = datetime.now(UTC) - timedelta(seconds=150)
        self.shutdown_exit_code: int | None = None
        self.system_service = SystemService(self)


@pytest.mark.asyncio
async def test_health_reports_all_fields() -> None:
    container = FakeContainer()
    health = await container.system_service.health()

    assert health["bot_online"] is True
    assert health["ping_ms"] == 120.0
    assert health["database"] == "ok"
    assert health["scheduler"] == "running"
    assert health["voice_connections"] == 2
    assert health["uptime_seconds"] == 150
    assert health["version"]


@pytest.mark.asyncio
async def test_health_reflects_database_failure() -> None:
    container = FakeContainer(db_ok=False)
    health = await container.system_service.health()

    assert health["database"] == "error"


@pytest.mark.asyncio
async def test_health_reflects_scheduler_stopped() -> None:
    container = FakeContainer(scheduler_running=False)
    health = await container.system_service.health()

    assert health["scheduler"] == "stopped"


@pytest.mark.asyncio
async def test_shutdown_sets_exit_zero_and_closes_bot() -> None:
    container = FakeContainer()

    await container.system_service.shutdown("테스트")

    assert container.shutdown_exit_code == 0
    assert container.bot.closed is True


@pytest.mark.asyncio
async def test_restart_sets_restart_code_and_closes_bot() -> None:
    container = FakeContainer()

    await container.system_service.restart("테스트")

    assert container.shutdown_exit_code == RESTART_EXIT_CODE
    assert container.shutdown_exit_code == 42
    assert container.bot.closed is True
