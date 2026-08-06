"""Tests for VoiceManager shutdown / connection tracking."""

from __future__ import annotations

import pytest

from voice.manager import GuildConnection, VoiceManager


class FakeVoiceClient:
    def __init__(self, connected: bool = True) -> None:
        self._connected = connected
        self.disconnected = False

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        self.disconnected = True


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
