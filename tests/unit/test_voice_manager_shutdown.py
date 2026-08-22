"""Tests for VoiceManager shutdown / connection tracking."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from voice.manager import GuildConnection, VoiceManager


class FakeVoiceClient:
    def __init__(self, connected: bool = True, channel=None) -> None:
        self._connected = connected
        self.disconnected = False
        self.channel = channel

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        self.disconnected = True
        self._connected = False


class FakeAudio:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _make_manager() -> VoiceManager:
    return VoiceManager(bot=object(), auto_cleanup=False)


@pytest.mark.asyncio
async def test_shutdown_all_disconnects_every_connection() -> None:
    manager = _make_manager()
    voice = FakeVoiceClient()
    audio = FakeAudio()
    manager._connections[123] = GuildConnection(guild_id=123, voice_client=voice, audio=audio)

    assert manager.connection_count == 1
    await manager.shutdown_all()

    assert manager.connection_count == 0
    assert voice.disconnected is True
    assert audio.closed is True


@pytest.mark.asyncio
async def test_shutdown_all_is_noop_when_no_connections() -> None:
    manager = _make_manager()

    await manager.shutdown_all()

    assert manager.connection_count == 0


class _CapturingBot:
    def __init__(self, user_id: int = 99) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.handler = None

    def event(self, func):
        self.handler = func
        return func


def _voice_state(*, channel=None, guild=None) -> SimpleNamespace:
    return SimpleNamespace(channel=channel, guild=guild)


@pytest.mark.asyncio
async def test_auto_leave_when_last_human_leaves_channel() -> None:
    bot = _CapturingBot()
    manager = VoiceManager(bot=bot, auto_cleanup=True)
    channel = SimpleNamespace(members=[SimpleNamespace(bot=True, id=bot.user.id)])
    voice = FakeVoiceClient(channel=channel)
    audio = FakeAudio()
    manager._connections[123] = GuildConnection(guild_id=123, voice_client=voice, audio=audio)

    member = SimpleNamespace(id=7, guild=SimpleNamespace(id=123))
    before = _voice_state(channel=channel, guild=member.guild)
    after = _voice_state(channel=None, guild=None)

    await bot.handler(member, before, after)

    assert manager.connection_count == 0
    assert voice.disconnected is True
    assert audio.closed is True


@pytest.mark.asyncio
async def test_auto_leave_stays_when_another_human_remains() -> None:
    bot = _CapturingBot()
    manager = VoiceManager(bot=bot, auto_cleanup=True)
    remaining = SimpleNamespace(bot=False, id=8)
    channel = SimpleNamespace(members=[remaining, SimpleNamespace(bot=True, id=bot.user.id)])
    voice = FakeVoiceClient(channel=channel)
    audio = FakeAudio()
    manager._connections[123] = GuildConnection(guild_id=123, voice_client=voice, audio=audio)

    member = SimpleNamespace(id=7, guild=SimpleNamespace(id=123))
    before = _voice_state(channel=channel, guild=member.guild)
    after = _voice_state(channel=None, guild=None)

    await bot.handler(member, before, after)

    assert manager.connection_count == 1
    assert voice.disconnected is False
    assert audio.closed is False
