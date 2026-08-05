"""Base cog shared by all features.

Provides a common construction pattern and a stub-response helper so
feature command skeletons behave identically.
"""

from __future__ import annotations

from discord.ext import commands
from discord.ext.commands import Bot

from core.logger import get_logger

NOT_IMPLEMENTED_MESSAGE = "아직 구현되지 않은 기능입니다."


class FeatureCog(commands.Cog):
    """Base class for feature cogs.

    Subclasses receive their service dependencies via constructor injection.
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.log = get_logger(type(self).__name__)

    async def _not_implemented(self, ctx) -> None:
        """Reply with the standard 'not implemented' message."""
        # Support both Context and Interaction
        if hasattr(ctx, "followup"):
            # Interaction
            await ctx.followup.send(NOT_IMPLEMENTED_MESSAGE)
        else:
            # Context
            await ctx.send(NOT_IMPLEMENTED_MESSAGE)
