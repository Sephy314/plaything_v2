"""TTS feature service — orchestrates voice join and message-to-speech."""

from __future__ import annotations

from typing import Any

from core.exceptions import VoiceException
from core.logger import get_logger
from voice.manager import VoiceManager
from voice.tts.provider import detect_language

log = get_logger(__name__)


class TtsFeatureService:
    """Business logic for the TTS feature.

    Args:
        voice_manager: The guild voice manager.
        tts_voice_service: Resolves per-user TTS voice settings.
        prefix: The bot command prefix used to ignore commands.
    """

    def __init__(
        self,
        voice_manager: VoiceManager,
        tts_voice_service: Any,
        prefix: str = "!",
    ) -> None:
        self._voice_manager = voice_manager
        self._tts_voice_service = tts_voice_service
        self._prefix = prefix
        self._enabled: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Channel activation
    # ------------------------------------------------------------------

    def enable_channel(self, guild_id: int, channel_id: int) -> None:
        """Start reading messages from a text channel for speech."""
        self._enabled[guild_id] = channel_id
        log.info("tts enabled for guild %s channel %s", guild_id, channel_id)

    def is_enabled(self, guild_id: int) -> bool:
        """Whether TTS is reading messages in the given guild."""
        return guild_id in self._enabled

    def target_channel(self, guild_id: int) -> int | None:
        """Return the TTS text channel id for a guild, if enabled."""
        return self._enabled.get(guild_id)

    def is_command(self, text: str) -> bool:
        """Return whether the text is a bot command (starts with prefix)."""
        return text.startswith(self._prefix)

    def disable(self, guild_id: int) -> None:
        """Stop reading messages for a guild."""
        self._enabled.pop(guild_id, None)

    # ------------------------------------------------------------------
    # Voice lifecycle
    # ------------------------------------------------------------------

    async def join(self, ctx, channel) -> Any:
        """Join the given voice channel for the command's guild.

        Args:
            ctx: The command context.
            channel: The voice channel to join.

        Returns:
            The resulting guild connection.
        """
        return await self._voice_manager.join(ctx, channel)

    async def leave(self, ctx) -> None:
        """Disconnect voice and disable TTS for the command's guild."""
        guild_id = self._resolve_guild_id(ctx)
        self.disable(guild_id)
        await self._voice_manager.leave(ctx)

    # ------------------------------------------------------------------
    # Speaking
    # ------------------------------------------------------------------

    async def speak(self, ctx, text: str) -> None:
        """Speak text on the guild's audio connection.

        Args:
            ctx: The message/command context.
            text: The text to speak.

        Raises:
            VoiceException: If the bot is not connected to voice.
        """
        connection = self._voice_manager.get_connection(ctx)
        if connection is None or connection.audio is None or not connection.is_connected:
            raise VoiceException("Bot이 음성 채널에 연결되어 있지 않습니다.")

        setting = await self._tts_voice_service.get_voice(self._resolve_user_id(ctx))
        voice = setting.voice_id if setting else "default"
        language = detect_language(text)
        connection.audio.speak(text, voice, language)
        log.info("tts speak requested: %r (voice=%s lang=%s)", text, voice, language)

    # ------------------------------------------------------------------
    # Voice settings
    # ------------------------------------------------------------------

    async def set_voice(self, ctx, voice_id: str) -> Any:
        """Persist the user's TTS voice preference.

        Args:
            ctx: The command context.
            voice_id: The requested voice identifier.

        Returns:
            The saved setting.
        """
        return await self._tts_voice_service.set_voice(self._resolve_user_id(ctx), voice_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_guild_id(ctx) -> int:
        guild = getattr(ctx, "guild", None)
        if guild is None:
            raise VoiceException("명령은 서버에서만 사용할 수 있습니다.")
        return int(guild.id)

    @staticmethod
    def _resolve_user_id(ctx) -> int:
        return int(ctx.author.id)
