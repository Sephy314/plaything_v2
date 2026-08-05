"""TTS feature commands.

Real text-to-speech playback is implemented in a later stage.
"""

from __future__ import annotations

from discord.ext.commands import Bot, Context, command

from core.container import get_container
from features.base import FeatureCog
from services.voice_service import VoiceService


class TtsCog(FeatureCog):
    """Prefix commands for text-to-speech playback."""

    def __init__(self, bot: Bot, service: VoiceService) -> None:
        super().__init__(bot)
        self._service = service

    @command(name="tts", help="Speak the given text in voice.")
    async def tts(self, ctx: Context, *, text: str) -> None:
        """Play the supplied text as speech."""
        await self._not_implemented(ctx)

    @command(name="voice", help="Set your preferred TTS voice.")
    async def voice(self, ctx: Context, voice_id: str) -> None:
        """Configure the user's voice preference."""
        await self._not_implemented(ctx)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    container = get_container()
    bot.add_cog(TtsCog(bot, container.voice_service))
