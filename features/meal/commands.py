"""Meal feature commands — debugging & integration-testing only.

The daily meal is normally published automatically by the scheduler (07:00
Asia/Seoul) with **no** user-facing command. These slash commands exist purely
so operators can trigger the pipeline on demand:

* verify the HTTP fetch + parser end-to-end,
* see the exact message that would be posted,
* exercise the configured ``MEAL_CHANNEL_ID`` send path,
* emit the ``Meal Fetch Success / Failed`` log events.

Run ``/급식`` or ``/급식날짜 <년> <월> <일>`` from any channel.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext.commands import Bot

from core.logger import get_logger
from features.base import FeatureCog
from features.meal.service import MealService

log = get_logger(__name__)


class MealCog(FeatureCog):
    """Slash commands to trigger and inspect the meal pipeline."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._service: MealService | None = None

    @property
    def service(self) -> MealService:
        """Lazy-load the shared :class:`MealService` from the container."""
        if self._service is None:
            from core.container import get_container

            self._service = get_container().meal_service
        return self._service

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="급식",
        description="오늘의 급식을 조회·출력합니다 (디버깅/통합 테스트)",
    )
    async def meal(self, interaction: discord.Interaction) -> None:
        """Fetch and publish today's meal (debug/integration-test command)."""
        await interaction.response.defer()
        await self._run(interaction, today=None)

    @app_commands.command(
        name="급식날짜",
        description="특정 날짜의 급식을 조회·출력합니다 (디버깅/통합 테스트)",
    )
    @app_commands.describe(year="년도", month="월", day="일")
    async def meal_date(
        self,
        interaction: discord.Interaction,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
    ) -> None:
        """Fetch and publish the meal for a specific date."""
        await interaction.response.defer()
        date_str = self._compose_date(year, month, day)
        if date_str is None:
            await interaction.followup.send(
                "❌ 날짜를 올바르게 입력해주세요. 예: `/급식날짜 2026 8 6`"
            )
            return
        await self._run(interaction, today=date_str)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run(
        self,
        interaction: discord.Interaction,
        *,
        today: str | None,
    ) -> None:
        """Run the meal pipeline and report the outcome to the caller.

        ``send_meal`` also posts to the configured meal channel when one is
        set; the reply here shows the exact message for debugging.
        """
        try:
            result = await self.service.send_meal(today)
        except Exception as exc:
            self.log.error("meal command failed: %s", exc, exc_info=exc)
            await interaction.followup.send(f"❌ 급식 조회에 실패했습니다: {exc}")
            return

        if result is None:
            await interaction.followup.send("🍽️ 해당 날짜의 급식이 없습니다.")
            return

        await interaction.followup.send(self.service.format_message(result))

    @staticmethod
    def _compose_date(year: int | None, month: int | None, day: int | None) -> str | None:
        """Build a ``YYYYMMDD`` string from optional year/month/day parts."""
        if year is None and month is None and day is None:
            return None
        if year is None or month is None or day is None:
            return None
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return f"{year:04d}{month:02d}{day:02d}"


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(MealCog(bot))
