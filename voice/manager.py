"""Guild-level voice connection manager.

Tracks the active :class:`VoiceClient` and an :class:`AudioManager` per
guild, and provides join / leave / get_connection / is_connected lifecycle
methods plus automatic cleanup on disconnect.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from discord import VoiceClient, VoiceState
from discord.ext.commands import Bot

from core.exceptions import VoiceException
from core.logger import get_logger
from voice.audio.manager import AudioManager

log = get_logger(__name__)

GUILD_ID_PATTERN = re.compile(r"^\d{17,20}$")


@dataclass
class GuildConnection:
    """Holds the state of a guild's voice connection.

    Attributes:
        guild_id: The Discord guild snowflake id.
        voice_client: The active voice client, if connected.
        audio: The :class:`AudioManager` bound to this connection, if any.
    """

    guild_id: int
    voice_client: VoiceClient | None = None
    audio: AudioManager | None = field(default=None)

    @property
    def is_connected(self) -> bool:
        """Whether the wrapped voice client is connected."""
        return bool(self.voice_client and self.voice_client.is_connected())


class VoiceManager:
    """Owns and tracks voice clients and audio managers across guilds.

    Args:
        bot: The Discord bot.
        audio_factory: Optional callable returning an :class:`AudioManager`
            for a given voice client (used for dependency injection / tests).
        ffmpeg: FFmpeg executable used when a default audio manager is built.
        auto_cleanup: Register a disconnect handler when True.
    """

    def __init__(
        self,
        bot: Bot,
        *,
        audio_factory: Callable[[Any], AudioManager] | None = None,
        ffmpeg: str = "ffmpeg",
        auto_cleanup: bool = True,
    ) -> None:
        self._bot = bot
        self._audio_factory = audio_factory
        self._ffmpeg = ffmpeg
        self._connections: dict[int, GuildConnection] = {}
        self._lock = asyncio.Lock()
        if auto_cleanup:
            self._attach_disconnect_handler()

    def _attach_disconnect_handler(self) -> None:
        """Register an event that cleans up connections on voice disconnect."""

        @self._bot.event
        async def on_voice_state_update(
            member,
            before: VoiceState,
            after: VoiceState,
        ) -> None:
            if member.id != self._bot.user.id:
                return
            if not before.channel and after.channel:
                return
            if before.channel and not after.channel:
                await self._drop(self._connection_for_guild_id(after.guild.id))

    def _connection_for_guild_id(self, guild_id: int) -> GuildConnection | None:
        return self._connections.get(guild_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def join(self, ctx_or_guildid, channel) -> GuildConnection:
        """Join (or move to) a voice channel for a guild.

        Args:
            ctx_or_guildid: A command context or raw guild id.
            channel: The voice channel to join.

        Returns:
            The :class:`GuildConnection` for the guild.

        Raises:
            VoiceException: If no valid guild id can be determined.
        """
        return await self.connect(ctx_or_guildid, channel)

    async def connect(self, ctx_or_guildid, channel) -> GuildConnection:
        """Open a voice connection and audio manager for a guild."""
        guild_id = self._resolve_guild_id(ctx_or_guildid)
        self._validate_guild_id(guild_id)

        async with self._lock:
            connection = self._connections.get(guild_id)
            if connection and connection.voice_client:
                await self._move_voice_channel(connection, channel)
                return connection

            voice_client = await channel.connect()
            audio = self._build_audio(voice_client)
            connection = GuildConnection(
                guild_id=guild_id,
                voice_client=voice_client,
                audio=audio,
            )
            self._connections[guild_id] = connection
            log.info("voice joined in guild %s", guild_id)
            return connection

    async def leave(self, ctx_or_guildid) -> None:
        """Disconnect the voice client and audio for a guild, if connected."""
        guild_id = self._resolve_guild_id(ctx_or_guildid)
        connection = self._connections.get(guild_id)
        if connection is None or connection.voice_client is None:
            log.warning("no active connection for guild %s to leave", guild_id)
            return
        await self._drop(connection)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_connection(self, ctx_or_guildid) -> GuildConnection | None:
        """Return the tracked connection for a guild, if any."""
        return self._connections.get(self._resolve_guild_id(ctx_or_guildid))

    def is_connected(self, ctx_or_guildid) -> bool:
        """Return whether the given guild has an active connection."""
        connection = self.get_connection(ctx_or_guildid)
        return bool(connection and connection.is_connected)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_audio(self, voice_client: Any) -> AudioManager:
        if self._audio_factory is not None:
            return self._audio_factory(voice_client)
        return AudioManager(voice_client, ffmpeg=self._ffmpeg)

    async def _move_voice_channel(self, connection: GuildConnection, channel) -> None:
        """Relocate an existing connection to another channel, if different."""
        if connection.voice_client and connection.voice_client.channel != channel:
            await connection.voice_client.move_to(channel)

    async def _drop(self, connection: GuildConnection | None) -> None:
        """Remove a connection, close audio and disconnect voice."""
        if connection is None:
            return
        guild_id = connection.guild_id
        voice_client = connection.voice_client
        self._connections.pop(guild_id, None)
        if connection.audio is not None:
            try:
                connection.audio.close()
            except Exception:  # pragma: no cover - defensive
                log.exception("failed to close audio in guild %s", guild_id)
            connection.audio = None
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect()
            except Exception:  # pragma: no cover - defensive
                log.exception("failed to disconnect voice in guild %s", guild_id)
        log.info("voice left in guild %s", guild_id)

    def _resolve_guild_id(self, ctx_or_guildid) -> int:
        if isinstance(ctx_or_guildid, int):
            return ctx_or_guildid
        guild = getattr(ctx_or_guildid, "guild", None)
        if guild is None:
            raise VoiceException("Unable to resolve guild id from context")
        return int(guild.id)

    def _validate_guild_id(self, guild_id: int) -> None:
        if not GUILD_ID_PATTERN.match(str(guild_id)):
            raise VoiceException(f"Invalid guild id: {guild_id!r}")
