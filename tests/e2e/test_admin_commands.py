"""E2E-style tests for the admin cog command flows.

Exercises the slash command handlers end-to-end against a fake system
service: confirmations are sent, shutdown/restart reasons are recorded, and
the health snapshot is rendered.
"""

from __future__ import annotations

import pytest

from features.admin import commands as admin_commands
from features.admin.commands import AdminCog

HEALTH = {
    "bot_online": True,
    "ping_ms": 25.5,
    "database": "ok",
    "scheduler": "running",
    "voice_connections": 1,
    "uptime_seconds": 3661,
    "version": "0.1.0",
}


class FakeSystem:
    def __init__(self, health: dict | None = None) -> None:
        self.shutdown_reasons: list[str] = []
        self.restart_reasons: list[str] = []
        self.health_result = health or HEALTH

    async def shutdown(self, reason: str) -> None:
        self.shutdown_reasons.append(reason)

    async def restart(self, reason: str) -> None:
        self.restart_reasons.append(reason)

    async def health(self) -> dict:
        return self.health_result

    async def log_channel_status(self) -> dict:
        return {
            "channel_id": 1534900319039918166,
            "configured": True,
            "found": True,
            "name": "logs",
            "mention": "<#1534900319039918166>",
            "type": "TextChannel",
            "log_channel_id": 1534900319039918166,
        }

    async def meal_channel_status(self) -> dict:
        return {
            "channel_id": 1534900319039918166,
            "configured": True,
            "found": True,
            "name": "meal",
            "mention": "<#1534900319039918166>",
            "type": "TextChannel",
            "meal_channel_id": 0,
            "log_channel_id": 1534900319039918166,
        }


class FakeBot:
    def event(self, func):
        return func


class FakeResponse:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.deferred = False

    async def send_message(self, message: str) -> None:
        self.sent.append(message)

    async def defer(self) -> None:
        self.deferred = True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def _make_cog(system: FakeSystem | None = None) -> AdminCog:
    return AdminCog(FakeBot(), system or FakeSystem())


@pytest.mark.asyncio
async def test_slash_shutdown_confirms_and_requests_shutdown(monkeypatch) -> None:
    monkeypatch.setattr(admin_commands, "_CONFIRM_DELAY", 0)
    system = FakeSystem()
    cog = _make_cog(system)
    interaction = FakeInteraction()

    await cog.slash_shutdown.callback(cog, interaction)

    assert interaction.response.sent
    assert system.shutdown_reasons == ["관리자 명령 (/봇_종료)"]
    assert system.restart_reasons == []


@pytest.mark.asyncio
async def test_slash_restart_confirms_and_requests_restart(monkeypatch) -> None:
    monkeypatch.setattr(admin_commands, "_CONFIRM_DELAY", 0)
    system = FakeSystem()
    cog = _make_cog(system)
    interaction = FakeInteraction()

    await cog.slash_restart.callback(cog, interaction)

    assert interaction.response.sent
    assert system.restart_reasons == ["관리자 명령 (/봇_재시작)"]


@pytest.mark.asyncio
async def test_slash_status_renders_health() -> None:
    cog = _make_cog(FakeSystem(health=HEALTH))
    interaction = FakeInteraction()

    await cog.slash_status.callback(cog, interaction)

    assert interaction.response.deferred is True
    assert interaction.followup.messages
    assert "봇 상태" in interaction.followup.messages[0]
    assert "25.5ms" in interaction.followup.messages[0]


def test_format_uptime() -> None:
    assert AdminCog._format_uptime(0) == "0초"
    assert AdminCog._format_uptime(59) == "59초"
    assert AdminCog._format_uptime(3661) == "1시간 1분 1초"
    assert AdminCog._format_uptime(90061) == "1일 1시간 1분 1초"


@pytest.mark.asyncio
async def test_slash_debug_log_channel_renders_status() -> None:
    cog = _make_cog(FakeSystem())
    interaction = FakeInteraction()

    await cog.slash_debug_log_channel.callback(cog, interaction)

    assert interaction.response.deferred is True
    assert "로그 채널" in interaction.followup.messages[0]
    assert "🟢" in interaction.followup.messages[0]


@pytest.mark.asyncio
async def test_slash_debug_meal_channel_renders_status() -> None:
    cog = _make_cog(FakeSystem())
    interaction = FakeInteraction()

    await cog.slash_debug_meal_channel.callback(cog, interaction)

    assert "급식 채널" in interaction.followup.messages[0]
    assert "🟢" in interaction.followup.messages[0]


@pytest.mark.asyncio
async def test_slash_debug_meal_channel_unresolved_renders_error() -> None:
    system = FakeSystem()

    async def unresolved() -> dict:
        return {
            "channel_id": 999,
            "configured": True,
            "found": False,
            "name": None,
            "mention": None,
            "type": None,
            "meal_channel_id": 999,
            "log_channel_id": 0,
        }

    system.meal_channel_status = unresolved
    cog = _make_cog(system)
    interaction = FakeInteraction()

    await cog.slash_debug_meal_channel.callback(cog, interaction)

    assert "🔴" in interaction.followup.messages[0]
    assert "999" in interaction.followup.messages[0]
