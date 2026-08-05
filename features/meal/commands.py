"""Meal feature commands.

Real meal fetching from the school API is implemented in a later stage.
"""

from __future__ import annotations

from discord.ext.commands import Bot, Context, command

from features.base import FeatureCog


class MealCog(FeatureCog):
    """Prefix commands for school meal information."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

    @command(name="급식", help="Show today's school meal.")
    async def meal(self, ctx: Context, date: str | None = None) -> None:
        """Display meal information for the given or today's date."""
        await self._not_implemented(ctx)

    @command(name="급식날짜", help="Show the meal for a specific YYYYMMDD date.")
    async def meal_date(
        self,
        ctx: Context,
        year: int = 0,
        month: int = 0,
        day: int = 0,
    ) -> None:
        """Display meal information for a date given as year/month/day."""
        del year, month, day  # kept for forward-compatible signature
        await self._not_implemented(ctx)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(MealCog(bot))
