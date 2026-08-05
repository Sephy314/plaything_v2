"""Guild-level voice connection manager.

Tracks the active :class:`VoiceClient` per guild and provides connect /
disconnect lifecycle methods. Actual audio streaming is not implemented in
this stage — playback will be handled by an ``AudioManager`` later.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from discord import VoiceClient, VoiceState
from discord.ext.commands import Bot

from core.exceptions import VoiceException
from core.logger import get_logger

log = get_logger(__name__)

GUILD_ID_PATTERN = re.compile(r"^\d{17,20}$")


@dataclass
class GuildConnection:
    """Holds the state of a guild's voice connection.

    Attributes:
        guild_id: The Discord guild snowflake id.
        voice_client: The active voice client, if connected.
    """

    guild_id: int
    voice_client: VoiceClient | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the wrapped voice client is connected."""
        return bool(self.voice_client and self.voice_client.is_connected())


class VoiceManager:
    """Owns and tracks voice clients across guilds."""

    def __init__(self, bot: Bot, *, auto_cleanup: bool = True) -> None:
        self._bot = bot
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

    async def connect(self, ctx_or_guildid, channel) -> GuildConnection:
        """Open a voice connection for a guild.

        Args:
            ctx_or_guildid: A command context or raw guild id.
            channel: The voice channel to join.

        Returns:
            The :class:`GuildConnection` for the guild.

        Raises:
            VoiceException: If no valid guild id can be determined.
        """
        guild_id = self._resolve_guild_id(ctx_or_guildid)
        self._validate_guild_id(guild_id)

        async with self._lock:
            connection = self._connections.get(guild_id)
            if connection and connection.voice_client:
                await self._move_voice_channel(connection, channel)
                return connection

            voice_client = await channel.connect()
            connection = GuildConnection(guild_id=guild_id, voice_client=voice_client)
            self._connections[guild_id] = connection
            log.info("connected to voice in guild %s", guild_id)
            return connection

    async def leave(self, ctx_or_guildid) -> None:
        """Disconnect the voice client for a guild, if connected.

        Args:
            ctx_or_guildid: A command context or raw guild id.
        """
        guild_id = self._resolve_guild_id(ctx_or_guildid)
        connection = self._connections.get(guild_id)
        if connection is None or connection.voice_client is None:
            log.warning("no active connection for guild %s to leave", guild_id)
            return
        await connection.voice_client.disconnect()
        await self._drop(connection)

    async def _move_voice_channel(self, connection: GuildConnection, channel) -> None:
        """Relocate an existing connection to another channel, if different."""
        if connection.voice_client and connection.voice_client.channel != channel:
            await connection.voice_client.move_to(channel)

    async def _drop(self, connection: GuildConnection | None) -> None:
        """Remove a connection from tracking and disconnect it."""
        if connection is None:
            return
        guild_id = connection.guild_id
        voice_client = connection.voice_client
        self._connections.pop(guild_id, None)
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect()
            except VoiceException:  # pragma: no cover - defensive
                log.exception("failed to disconnect voice in guild %s", guild_id)

    def is_connected(self, ctx_or_guildid) -> bool:
        """Return whether the given guild has an active connection.

        Args:
            ctx_or_guildid: A command context or raw guild id.
        """
        guild_id = self._resolve_guild_id(ctx_or_guildid)
        connection = self._connections.get(guild_id)
        return bool(
            connection and connection.voice_client and connection.voice_client.is_connected()
        )

    def get_connection(self, ctx_or_guildid) -> GuildConnection | None:
        """Return the tracked connection for a guild, if any.

        Args:
            ctx_or_guildid: A command context or raw guild id.
        """
        return self._connections.get(self._resolve_guild_id(ctx_or_guildid))

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
