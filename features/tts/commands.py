"""TTS feature commands — voice join and message-to-speech."""

from __future__ import annotations

import discord
from discord.ext.commands import Bot, Context, group

from core.exceptions import VoiceException
from core.logger import get_logger
from features.base import FeatureCog
from features.tts.service import TtsFeatureService

log = get_logger(__name__)


class TtsCog(FeatureCog):
    """Prefix commands for text-to-speech playback."""

    def __init__(self, bot: Bot, service: TtsFeatureService) -> None:
        super().__init__(bot)
        self._service = service
        bot.add_listener(self._on_message, "on_message")

    # ------------------------------------------------------------------
    # Voice join
    # ------------------------------------------------------------------

    @group(name="TTS", invoke_without_command=True, help="TTS 음성 기능")
    async def tts_group(self, ctx: Context) -> None:
        """Show TTS usage."""
        await ctx.send("`!TTS 입장` — 현재 음성 채널에 입장 후 메시지를 읽습니다.")

    @tts_group.command(name="입장", help="음성 채널에 입장하고 메시지를 읽기 시작합니다.")
    async def tts_join(self, ctx: Context) -> None:
        """Join the caller's voice channel and start reading messages."""
        voice_channel = getattr(ctx.author.voice, "channel", None)
        if voice_channel is None:
            await ctx.send("먼저 음성 채널에 들어가 있어야 합니다.")
            return
        try:
            await self._service.join(ctx, voice_channel)
        except VoiceException as exc:
            await ctx.send(f"음성 채널 입장에 실패했습니다: {exc}")
            return
        self._service.enable_channel(ctx.guild.id, ctx.channel.id)
        await ctx.send(f"TTS가 활성화되었습니다. <#{ctx.channel.id}> 채널의 메시지를 읽습니다.")

    # ------------------------------------------------------------------
    # Voice preference
    # ------------------------------------------------------------------

    @group(name="voice", invoke_without_command=True, help="TTS 음성 목소리 설정")
    async def voice_group(self, ctx: Context, voice_id: str) -> None:
        """Set the caller's TTS voice preference."""
        try:
            setting = await self._service.set_voice(ctx, voice_id)
        except ValueError as exc:
            await ctx.send(f"음성 설정에 실패했습니다: {exc}")
            return
        await ctx.send(f"음성 목소리를 설정했습니다: `{setting.voice_id}`")

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
        prefix=container.settings.bot_prefix if hasattr(container.settings, "bot_prefix") else "!",
    )
    await bot.add_cog(TtsCog(bot, service))
