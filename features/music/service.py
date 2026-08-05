"""Music feature service layer.

Handles YouTube URL extraction, queue management, and playback coordination
without depending on Discord context or commands.
"""

from __future__ import annotations

from typing import Any

from core.exceptions import VoiceException, YoutubeError
from core.logger import get_logger
from voice.music.youtube import Track

log = get_logger(__name__)


class MusicService:
    """Coordinates YouTube music playback via VoiceManager and AudioManager.

    Args:
        voice_manager: The application's :class:`VoiceManager`.
        youtube_client: The :class:`YoutubeClient` for URL extraction.
        log_channel_id: Discord channel ID for log messages, or 0 if disabled.
    """

    def __init__(
        self,
        voice_manager: Any,
        youtube_client: Any,
        *,
        log_channel_id: int = 0,
    ) -> None:
        self._voice_manager = voice_manager
        self._youtube = youtube_client
        self._log_channel_id = log_channel_id

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    async def play_music(
        self,
        guild_id: int,
        user_channel: Any,
        url: str,
        *,
        loop: bool = False,
    ) -> list[Track]:
        """Extract and queue YouTube tracks, joining the user's voice channel.

        Args:
            guild_id: The Discord guild snowflake ID.
            user_channel: The voice channel the user is in.
            url: A YouTube video or playlist URL.
            loop: Repeat the current track when True.

        Returns:
            The list of resolved :class:`Track` items.

        Raises:
            VoiceException: If the user is not in a voice channel or the bot
                cannot join.
            YoutubeError: If the URL cannot be resolved.
        """
        if not user_channel:
            raise VoiceException("You must be in a voice channel to play music")

        # Get or create the connection (VoiceManager.connect joins if needed)
        connection = await self._voice_manager.connect(guild_id, user_channel)
        if not connection or not connection.audio:
            raise VoiceException("Failed to join voice channel")

        # Play through AudioManager (handles TTS + music mixing)
        try:
            tracks = await connection.audio.play_music(url, loop=loop)
            log.info(
                "music queued: %d tracks from %r in guild %s",
                len(tracks),
                url,
                guild_id,
            )
            return tracks
        except YoutubeError:
            raise
        except Exception as exc:
            log.error("Failed to play music: %s", exc, exc_info=exc)
            raise YoutubeError(f"Playback failed: {exc}") from exc

    async def skip(self, guild_id: int) -> str | None:
        """Skip the current track.

        Args:
            guild_id: The Discord guild snowflake ID.

        Returns:
            The title of the next track if available, None if queue is empty.
        """
        connection = self._voice_manager.get_connection(guild_id)
        if not connection or not connection.audio:
            raise VoiceException("Not connected to a voice channel")

        connection.audio.skip()
        next_title = connection.audio._music.current_title
        log.info("skipped track in guild %s; next: %s", guild_id, next_title)
        return next_title

    async def stop(self, guild_id: int) -> None:
        """Stop all playback and leave the voice channel.

        Args:
            guild_id: The Discord guild snowflake ID.
        """
        connection = self._voice_manager.get_connection(guild_id)
        if not connection:
            raise VoiceException("Not connected to a voice channel")

        await self._voice_manager.leave(guild_id)
        log.info("stopped music and left voice channel in guild %s", guild_id)

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_queue_info(self, guild_id: int) -> dict[str, Any]:
        """Get current playback status.

        Args:
            guild_id: The Discord guild snowflake ID.

        Returns:
            A dict with keys: current_track, queue_size, looping, is_playing.
        """
        connection = self._voice_manager.get_connection(guild_id)
        if not connection or not connection.audio:
            return {
                "current_track": None,
                "queue_size": 0,
                "looping": False,
                "is_playing": False,
            }

        music = connection.audio._music
        return {
            "current_track": music.current_title,
            "queue_size": music.queue_size,
            "looping": music.looping,
            "is_playing": music.is_playing(),
        }
