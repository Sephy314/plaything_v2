"""Music feature commands.

Real audio playback is implemented in a later stage.
"""

from __future__ import annotations

from discord.ext.commands import Bot, Context, command

from core.container import get_container
from features.base import FeatureCog


class MusicCog(FeatureCog):
    """Prefix commands for music playback."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

    @command(name="play", help="Play music in voice.")
    async def play(self, ctx: Context, *, query: str) -> None:
        """Play the requested track."""
        await self._not_implemented(ctx)

    @command(name="skip", help="Skip the current track.")
    async def skip(self, ctx: Context) -> None:
        """Skip the currently playing track."""
        await self._not_implemented(ctx)

    @command(name="stop", help="Stop playback and leave voice.")
    async def stop(self, ctx: Context) -> None:
        """Stop playback and leave the voice channel."""
        await self._not_implemented(ctx)

    @command(name="queue", help="Show the current queue.")
    async def queue(self, ctx: Context) -> None:
        """Display the current playback queue."""
        await self._not_implemented(ctx)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    get_container()
    bot.add_cog(MusicCog(bot))
