"""Music feature commands.

Real audio playback is implemented in a later stage.
"""

from __future__ import annotations

from discord import app_commands
from discord.ext.commands import Bot

from core.container import get_container
from features.base import FeatureCog


class MusicCog(FeatureCog):
    """Slash commands for music playback."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

    @app_commands.command(name="play", description="음악을 재생합니다.")
    @app_commands.describe(query="검색어 또는 URL")
    async def play(self, interaction, query: str) -> None:
        """Play the requested track."""
        await interaction.response.defer()
        await self._not_implemented(interaction)

    @app_commands.command(name="skip", description="현재 곡을 스킵합니다.")
    async def skip(self, interaction) -> None:
        """Skip the currently playing track."""
        await interaction.response.defer()
        await self._not_implemented(interaction)

    @app_commands.command(name="stop", description="재생을 중지하고 음성 채널을 나갑니다.")
    async def stop(self, interaction) -> None:
        """Stop playback and leave the voice channel."""
        await interaction.response.defer()
        await self._not_implemented(interaction)

    @app_commands.command(name="queue", description="현재 재생 목록을 표시합니다.")
    async def queue(self, interaction) -> None:
        """Display the current playback queue."""
        await interaction.response.defer()
        await self._not_implemented(interaction)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    get_container()
    await bot.add_cog(MusicCog(bot))
