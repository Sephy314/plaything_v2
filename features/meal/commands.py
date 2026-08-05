"""Meal feature commands.

Real meal fetching from the school API is implemented in a later stage.
"""

from __future__ import annotations

from discord import app_commands
from discord.ext.commands import Bot

from features.base import FeatureCog


class MealCog(FeatureCog):
    """Slash commands for school meal information."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

    @app_commands.command(name="급식", description="오늘의 급식 정보를 표시합니다.")
    async def meal(self, interaction) -> None:
        """Display meal information for today."""
        await interaction.response.defer()
        await self._not_implemented(interaction)

    @app_commands.command(name="급식날짜", description="특정 날짜의 급식을 표시합니다.")
    @app_commands.describe(year="년도", month="월", day="일")
    async def meal_date(
        self,
        interaction,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
    ) -> None:
        """Display meal information for a date given as year/month/day."""
        await interaction.response.defer()
        del year, month, day
        await self._not_implemented(interaction)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(MealCog(bot))
