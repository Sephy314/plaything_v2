"""Tests for YouTube music playback feature.

Tests queue management, player logic, service layer, and command integration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from core.exceptions import VoiceException, YoutubeError
from features.music.service import MusicService
from voice.music.player import MusicPlayer, Track

# ============================================================================
# Fakes and Mocks
# ============================================================================


class FakeYoutubeClient:
    """Mock YoutubeClient for testing."""

    def __init__(self, tracks: list[Track] | None = None) -> None:
        self.tracks = tracks or []
        self.extract_called_with: list[str] = []

    async def extract(self, url: str) -> list[Track]:
        """Mock extract that returns pre-configured tracks."""
        self.extract_called_with.append(url)
        if not self.tracks:
            raise YoutubeError("No tracks configured for mock")
        return self.tracks


class FakeAudioManager:
    """Mock AudioManager for testing."""

    def __init__(self, return_tracks: list[Track] | None = None) -> None:
        self._music = MusicPlayer("ffmpeg")
        self.return_tracks = return_tracks or []
        self.play_music_called_with: list[tuple] = []
        self.on_track_start_called_with = None
        self.skip_called = False
        self.close_called = False

    async def play_music(
        self,
        url: str,
        *,
        loop: bool = False,
        on_track_start=None,
    ) -> list[Track]:
        """Mock play_music."""
        self.play_music_called_with.append((url, loop))
        self.on_track_start_called_with = on_track_start
        # Return configured tracks
        return self.return_tracks

    def skip(self) -> None:
        """Mock skip."""
        self.skip_called = True

    def close(self) -> None:
        """Mock close."""
        self.close_called = True


class FakeGuildConnection:
    """Mock GuildConnection for testing."""

    def __init__(
        self,
        guild_id: int,
        audio: FakeAudioManager | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.audio = audio or FakeAudioManager()
        self.voice_client = Mock()
        self.is_connected = True


class FakeVoiceManager:
    """Mock VoiceManager for testing."""

    def __init__(self, audio_factory=None) -> None:
        self.connections: dict[int, FakeGuildConnection] = {}
        self.connect_called_with: list[tuple] = []
        self.leave_called_with: list[int] = []
        self._audio_factory = audio_factory

    async def connect(self, guild_id: int, channel) -> FakeGuildConnection:
        """Mock connect."""
        self.connect_called_with.append((guild_id, channel))
        if guild_id not in self.connections:
            if self._audio_factory:
                audio = self._audio_factory()
            else:
                audio = FakeAudioManager()
            self.connections[guild_id] = FakeGuildConnection(guild_id, audio=audio)
        return self.connections[guild_id]

    def get_connection(self, guild_id: int) -> FakeGuildConnection | None:
        """Mock get_connection."""
        return self.connections.get(guild_id)

    async def leave(self, guild_id: int) -> None:
        """Mock leave."""
        self.leave_called_with.append(guild_id)
        if guild_id in self.connections:
            del self.connections[guild_id]


# ============================================================================
# MusicPlayer Tests
# ============================================================================


def test_music_player_enqueue_single_track() -> None:
    """Enqueue adds track to queue."""
    player = MusicPlayer("ffmpeg")
    track = Track(url="http://example.com/video", title="Test Video")

    player.enqueue(track)

    assert player.queue_size == 1
    assert player.current_title is None


def test_music_player_enqueue_multiple_tracks() -> None:
    """Enqueue multiple tracks maintains order."""
    player = MusicPlayer("ffmpeg")
    tracks = [
        Track(url="http://example.com/1", title="Track 1"),
        Track(url="http://example.com/2", title="Track 2"),
        Track(url="http://example.com/3", title="Track 3"),
    ]

    for track in tracks:
        player.enqueue(track)

    assert player.queue_size == 3


def test_music_player_set_loop() -> None:
    """set_loop enables/disables loop mode."""
    player = MusicPlayer("ffmpeg")

    player.set_loop(True)
    assert player.looping is True

    player.set_loop(False)
    assert player.looping is False


def test_music_player_clear() -> None:
    """Clear removes all queued tracks."""
    player = MusicPlayer("ffmpeg")
    tracks = [
        Track(url="http://example.com/1", title="Track 1"),
        Track(url="http://example.com/2", title="Track 2"),
    ]

    for track in tracks:
        player.enqueue(track)

    player.clear()

    assert player.queue_size == 0


def test_music_player_close() -> None:
    """Close clears queue and resets state."""
    player = MusicPlayer("ffmpeg")
    track = Track(url="http://example.com/video", title="Test Video")
    player.enqueue(track)

    player.close()

    assert player.queue_size == 0
    assert player.is_playing() is False


# ============================================================================
# MusicService Tests
# ============================================================================


@pytest.mark.asyncio
async def test_music_service_play_music() -> None:
    """play_music joins channel and queues tracks."""
    track = Track(url="http://example.com/1", title="Track 1")

    def audio_factory():
        return FakeAudioManager(return_tracks=[track])

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=[track])
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    channel = Mock()
    tracks = await service.play_music(
        guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=123"
    )

    assert len(tracks) == 1
    assert len(voice_manager.connect_called_with) == 1
    assert voice_manager.connect_called_with[0] == (12345, channel)


@pytest.mark.asyncio
async def test_music_service_play_music_no_voice_channel() -> None:
    """play_music raises VoiceException if user not in channel."""
    voice_manager = FakeVoiceManager()
    youtube = FakeYoutubeClient()
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    with pytest.raises(VoiceException, match="voice channel"):
        await service.play_music(
            guild_id=12345, user_channel=None, url="http://youtube.com/watch?v=123"
        )


@pytest.mark.asyncio
async def test_music_service_play_music_with_loop() -> None:
    """play_music passes loop flag to AudioManager."""
    track = Track(url="http://example.com/1", title="Track 1")

    def audio_factory():
        return FakeAudioManager(return_tracks=[track])

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=[track])
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    channel = Mock()
    await service.play_music(
        guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=123", loop=True
    )

    connection = voice_manager.get_connection(12345)
    assert connection is not None
    # Check that play_music was called with loop=True
    assert connection.audio.play_music_called_with[0][1] is True  # Second element is loop flag


@pytest.mark.asyncio
async def test_music_service_play_music_forwards_on_track_start() -> None:
    """play_music forwards on_track_start to the AudioManager."""
    track = Track(url="http://example.com/1", title="Track 1")

    def audio_factory():
        return FakeAudioManager(return_tracks=[track])

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=[track])
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    def on_track_start(_track) -> None:
        pass

    channel = Mock()
    await service.play_music(
        guild_id=12345,
        user_channel=channel,
        url="http://youtube.com/watch?v=123",
        on_track_start=on_track_start,
    )

    connection = voice_manager.get_connection(12345)
    assert connection.audio.on_track_start_called_with is on_track_start


@pytest.mark.asyncio
async def test_music_service_skip() -> None:
    """skip calls music player skip method."""
    track = Track(url="http://example.com/1", title="Track 1")

    def audio_factory():
        return FakeAudioManager(return_tracks=[track])

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=[track])
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    # Setup a connection
    channel = Mock()
    await service.play_music(
        guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=123"
    )

    # Skip
    await service.skip(guild_id=12345)

    connection = voice_manager.get_connection(12345)
    assert connection.audio.skip_called is True


@pytest.mark.asyncio
async def test_music_service_skip_not_connected() -> None:
    """skip raises VoiceException if not connected."""
    voice_manager = FakeVoiceManager()
    youtube = FakeYoutubeClient()
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    with pytest.raises(VoiceException, match="voice channel"):
        await service.skip(guild_id=12345)


@pytest.mark.asyncio
async def test_music_service_stop() -> None:
    """stop leaves voice channel."""
    track = Track(url="http://example.com/1", title="Track 1")

    def audio_factory():
        return FakeAudioManager(return_tracks=[track])

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=[track])
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    # Setup a connection
    channel = Mock()
    await service.play_music(
        guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=123"
    )

    # Verify connection exists
    assert voice_manager.get_connection(12345) is not None

    # Stop
    await service.stop(guild_id=12345)

    # Verify leave was called
    assert 12345 in voice_manager.leave_called_with


@pytest.mark.asyncio
async def test_music_service_stop_not_connected() -> None:
    """stop raises VoiceException if not connected."""
    voice_manager = FakeVoiceManager()
    youtube = FakeYoutubeClient()
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    with pytest.raises(VoiceException, match="voice channel"):
        await service.stop(guild_id=12345)


def test_music_service_get_queue_info() -> None:
    """get_queue_info returns playback status."""
    voice_manager = FakeVoiceManager()
    youtube = FakeYoutubeClient()
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    # No connection
    info = service.get_queue_info(guild_id=12345)
    assert info["is_playing"] is False
    assert info["queue_size"] == 0
    assert info["looping"] is False


def test_music_service_get_queue_info_with_connection() -> None:
    """get_queue_info returns info when connected."""
    voice_manager = FakeVoiceManager()
    youtube = FakeYoutubeClient()
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    # Create a connection
    audio = FakeAudioManager()
    connection = FakeGuildConnection(guild_id=12345, audio=audio)
    voice_manager.connections[12345] = connection

    # Enqueue a track
    track = Track(url="http://example.com/video", title="Test Video")
    audio._music.enqueue(track)

    info = service.get_queue_info(guild_id=12345)
    assert info["queue_size"] == 1
    assert info["looping"] is False


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_music_service_integration_play_skip_stop() -> None:
    """Full workflow: play -> skip -> stop."""
    tracks = [
        Track(url="http://example.com/1", title="Track 1"),
        Track(url="http://example.com/2", title="Track 2"),
    ]

    def audio_factory():
        return FakeAudioManager(return_tracks=tracks)

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=tracks)
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    channel = Mock()

    # Play
    result_tracks = await service.play_music(
        guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=123"
    )
    assert len(result_tracks) == 2

    # Skip
    await service.skip(guild_id=12345)

    # Stop
    await service.stop(guild_id=12345)
    assert voice_manager.get_connection(12345) is None


@pytest.mark.asyncio
async def test_music_service_integration_playlist_loop() -> None:
    """Full workflow with loop enabled."""
    tracks = [
        Track(url="http://example.com/1", title="Track 1"),
        Track(url="http://example.com/2", title="Track 2"),
        Track(url="http://example.com/3", title="Track 3"),
    ]

    def audio_factory():
        audio = FakeAudioManager(return_tracks=tracks)
        # Simulate what AudioManager.play_music should do
        original_play = audio.play_music

        async def play_with_loop(
            url: str,
            *,
            loop: bool = False,
            on_track_start=None,
        ):
            if loop:
                audio._music.set_loop(True)
            return await original_play(url, loop=loop, on_track_start=on_track_start)

        audio.play_music = play_with_loop
        return audio

    voice_manager = FakeVoiceManager(audio_factory=audio_factory)
    youtube = FakeYoutubeClient(tracks=tracks)
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    channel = Mock()

    # Play with loop
    result_tracks = await service.play_music(
        guild_id=12345,
        user_channel=channel,
        url="http://youtube.com/playlist?list=ABC",
        loop=True,
    )

    assert len(result_tracks) == 3

    connection = voice_manager.get_connection(12345)
    assert connection.audio._music.looping is True


# ============================================================================
# AudioManager Cancellation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_audio_manager_stop_cancels_in_flight_play() -> None:
    """stop() cancels a play that is still being fetched (e.g. long playlist)."""
    from voice.audio.manager import AudioManager

    started = asyncio.Event()
    release = asyncio.Event()

    class SlowYoutubeClient:
        async def extract(self, url: str) -> list[Track]:
            started.set()
            await release.wait()
            return [Track(url="http://example.com/1", title="Track 1")]

    vc = Mock()
    vc.is_connected.return_value = True

    audio = AudioManager(vc, youtube=SlowYoutubeClient())
    play_task = asyncio.create_task(audio.play_music("http://example.com/playlist"))
    await asyncio.wait_for(started.wait(), timeout=1)

    # Simulate /나가 while the playlist is still being fetched.
    audio.stop()

    with pytest.raises(asyncio.CancelledError):
        await play_task

    # The fetch was interrupted: nothing was enqueued or streamed.
    assert audio._music.queue_size == 0
    assert audio._music.current_title is None
    vc.play.assert_not_called()


# ============================================================================
# Error Handling Tests
# ============================================================================


@pytest.mark.asyncio
async def test_music_service_youtube_error() -> None:
    """play_music properly handles YoutubeError from AudioManager."""
    voice_manager = FakeVoiceManager()

    # Create a mock that raises error when play_music is called
    async def failing_play(url: str, *, loop: bool = False, on_track_start=None):
        raise YoutubeError("Playback failed")

    def audio_factory():
        audio = FakeAudioManager()
        audio.play_music = failing_play
        return audio

    voice_manager._audio_factory = audio_factory

    youtube = FakeYoutubeClient(tracks=[Track(url="http://example.com/1", title="Track 1")])
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    channel = Mock()

    # Should propagate YoutubeError from AudioManager
    with pytest.raises(YoutubeError):
        await service.play_music(
            guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=invalid"
        )


@pytest.mark.asyncio
async def test_music_service_voice_connection_failure() -> None:
    """play_music handles AudioManager errors gracefully."""

    class FailingAudioManager:
        async def play_music(
            self,
            url: str,
            *,
            loop: bool = False,
            on_track_start=None,
        ) -> list[Track]:
            raise Exception("Connection lost")

    class FailingVoiceManager:
        async def connect(self, guild_id: int, channel) -> object:
            return type("obj", (), {"audio": FailingAudioManager()})()

    voice_manager = FailingVoiceManager()
    youtube = FakeYoutubeClient()
    service = MusicService(voice_manager, youtube, log_channel_id=0)

    channel = Mock()

    with pytest.raises(YoutubeError, match="Playback failed"):
        await service.play_music(
            guild_id=12345, user_channel=channel, url="http://youtube.com/watch?v=123"
        )
