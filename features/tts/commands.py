"""TTS feature commands — slash commands for voice join and message-to-speech."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext.commands import Bot

from core.exceptions import VoiceException
from core.logger import get_logger
from features.base import FeatureCog
from features.tts.service import TtsFeatureService

log = get_logger(__name__)


async def voice_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for voice ID parameter with language-specific voices."""
    try:
        from core.container import container
        voices = container.tts_voice_service.list_voices()
        
        # Filter voices by current input
        filtered = [v for v in voices if v.lower().startswith(current.lower())]
        
        # Create choices with language indicators
        choices = []
        for voice in filtered[:25]:
            # Add language indicator to display name
            lang_indicator = ""
            if voice.startswith("ko"):
                lang_indicator = " 🇰🇷"
            elif voice.startswith("en"):
                lang_indicator = " 🇺🇸"
            
            choices.append(
                app_commands.Choice(
                    name=f"{voice}{lang_indicator}",
                    value=voice,
                )
            )
        return choices
    except Exception as e:
        log.debug("voice_autocomplete error: %s", e)
        return []


class TtsCog(FeatureCog):
    """Slash commands for text-to-speech playback."""

    def __init__(self, bot: Bot, service: TtsFeatureService) -> None:
        super().__init__(bot)
        self._service = service
        bot.add_listener(self._on_message, "on_message")

    # ------------------------------------------------------------------
    # Voice join
    # ------------------------------------------------------------------

    @app_commands.command(name="tts_입장", description="음성 채널에 입장하고 메시지를 읽기 시작합니다.")
    async def tts_join(self, interaction: discord.Interaction) -> None:
        """Join the caller's voice channel and start reading messages."""
        await interaction.response.defer()
        voice_channel = getattr(interaction.user.voice, "channel", None)
        if voice_channel is None:
            await interaction.followup.send("먼저 음성 채널에 들어가 있어야 합니다.")
            return
        try:
            await self._service.join(interaction, voice_channel)
        except VoiceException as exc:
            await interaction.followup.send(f"음성 채널 입장에 실패했습니다: {exc}")
            return
        self._service.enable_channel(interaction.guild.id, interaction.channel.id)
        await interaction.followup.send(
            f"TTS가 활성화되었습니다. <#{interaction.channel.id}> 채널의 메시지를 읽습니다."
        )

    @app_commands.command(name="tts_나가기", description="음성 채널에서 나갑니다.")
    async def tts_leave(self, interaction: discord.Interaction) -> None:
        """Leave the voice channel and disable TTS."""
        await interaction.response.defer()
        try:
            await self._service.leave(interaction)
        except VoiceException as exc:
            await interaction.followup.send(f"음성 채널 퇴장에 실패했습니다: {exc}")
            return
        await interaction.followup.send("음성 채널에서 나갔습니다. TTS가 비활성화되었습니다.")

    # ------------------------------------------------------------------
    # Voice preference
    # ------------------------------------------------------------------

    @app_commands.command(name="voice", description="TTS 음성 목소리를 설정합니다.")
    @app_commands.describe(voice_id="목소리 ID")
    @app_commands.autocomplete(voice_id=voice_autocomplete)
    async def voice_set(self, interaction: discord.Interaction, voice_id: str) -> None:
        """Set the caller's TTS voice preference."""
        await interaction.response.defer()
        try:
            setting = await self._service.set_voice(interaction, voice_id)
        except ValueError as exc:
            await interaction.followup.send(f"음성 설정에 실패했습니다: {exc}")
            return
        await interaction.followup.send(f"음성 목소리를 설정했습니다: `{setting.voice_id}`")

    # ------------------------------------------------------------------
    # Message listener (auto-read)
    # ------------------------------------------------------------------

    async def _on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.author.bot:
            return
        if not self._service.is_enabled(message.guild.id):
            return
        if self._service.target_channel(message.guild.id) != message.channel.id:
            return
        if not message.content or self._service.is_command(message.content):
            return
        try:
            await self._service.speak(message, message.content)
        except VoiceException as exc:
            log.warning("tts message skipped: %s", exc)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    from core.container import container

    service = TtsFeatureService(
        container.voice_manager,
        container.tts_voice_service,
        prefix=container.settings.bot_prefix if hasattr(container.settings, "bot_prefix") else "/",
    )
    cog = TtsCog(bot, service)
    await bot.add_cog(cog)
