"""Admin / operations commands.

Provides administrator-only lifecycle commands (shutdown, restart) and a
health-check command available to everyone. Both slash commands and the legacy
``!`` prefix variants are registered.

Permission model:
- Shutdown / restart → Discord Administrator (or guild owner) via ``@admin_only``.
- Health status → available to any user.
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Bot

from core.logger import get_logger
from core.permissions import admin_only
from features.base import FeatureCog
from services.system_service import SystemService

log = get_logger(__name__)

#: Small delay so the confirmation message is flushed to Discord before the
#: bot closes its connection.
_CONFIRM_DELAY = 0.75


class AdminCog(FeatureCog):
    """Slash + prefix commands for bot lifecycle and status."""

    def __init__(self, bot: Bot, system_service: SystemService) -> None:
        super().__init__(bot)
        self._system = system_service

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(name="봇_종료", description="봇을 종료합니다. (관리자)")
    @admin_only()
    async def slash_shutdown(self, interaction: discord.Interaction) -> None:
        """Gracefully shut the bot down."""
        await self._confirm_and_run(
            interaction,
            message="🛑 봇을 종료합니다. 잠시만 기다려 주세요...",
            action=self._system.shutdown,
            reason="관리자 명령 (/봇_종료)",
        )

    @app_commands.command(name="봇_재시작", description="봇을 재시작합니다. (관리자)")
    @admin_only()
    async def slash_restart(self, interaction: discord.Interaction) -> None:
        """Gracefully restart the bot (container restart policy re-spawns it)."""
        await self._confirm_and_run(
            interaction,
            message="🔄 봇을 재시작합니다. 잠시만 기다려 주세요...",
            action=self._system.restart,
            reason="관리자 명령 (/봇_재시작)",
        )

    @app_commands.command(name="봇_상태", description="봇 상태를 확인합니다.")
    async def slash_status(self, interaction: discord.Interaction) -> None:
        """Reply with a health snapshot."""
        await interaction.response.defer()
        await interaction.followup.send(await self._format_health())

    # ------------------------------------------------------------------
    # Prefix commands (!봇 종료 / !봇 재시작 / !상태)
    # ------------------------------------------------------------------

    @commands.group(name="봇", invoke_without_command=True)
    async def admin_group(self, ctx: commands.Context) -> None:
        """Legacy ``!봇`` group: ``!봇 종료``, ``!봇 재시작``."""
        await ctx.send("사용법: `!봇 종료` (종료), `!봇 재시작` (재시작)")

    @admin_group.command(name="종료")
    @admin_only()
    async def prefix_shutdown(self, ctx: commands.Context) -> None:
        """Gracefully shut the bot down (``!봇 종료``)."""
        await self._confirm_and_run_ctx(
            ctx,
            message="🛑 봇을 종료합니다. 잠시만 기다려 주세요...",
            action=self._system.shutdown,
            reason="관리자 명령 (!봇 종료)",
        )

    @admin_group.command(name="재시작")
    @admin_only()
    async def prefix_restart(self, ctx: commands.Context) -> None:
        """Gracefully restart the bot (``!봇 재시작``)."""
        await self._confirm_and_run_ctx(
            ctx,
            message="🔄 봇을 재시작합니다. 잠시만 기다려 주세요...",
            action=self._system.restart,
            reason="관리자 명령 (!봇 재시작)",
        )

    @commands.command(name="상태")
    async def prefix_status(self, ctx: commands.Context) -> None:
        """Reply with a health snapshot (``!상태``)."""
        await ctx.send(await self._format_health())

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _format_health(self) -> str:
        try:
            health = await self._system.health()
        except Exception as exc:  # pragma: no cover - defensive
            log.error("health check failed: %s", exc, exc_info=exc)
            return "⚠️ 상태 조회 중 오류가 발생했습니다."

        def _dot(ok: bool) -> str:
            return "🟢" if ok else "🔴"

        db_ok = health["database"] == "ok"
        sched_ok = health["scheduler"] == "running"
        uptime = self._format_uptime(health["uptime_seconds"])
        online = "온라인" if health["bot_online"] else "오프라인"
        return (
            f"**봇 상태**\n"
            f"- {_dot(health['bot_online'])} 봇: {online}\n"
            f"- 📶 핑: `{health['ping_ms']}ms`\n"
            f"- {_dot(db_ok)} 데이터베이스: `{health['database']}`\n"
            f"- {_dot(sched_ok)} 스케줄러: `{health['scheduler']}`\n"
            f"- 🎙️ 음성 연결: `{health['voice_connections']}개`\n"
            f"- ⏱️ 업타임: `{uptime}`\n"
            f"- 🏷️ 버전: `{health['version']}`"
        )

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}일")
        if hours:
            parts.append(f"{hours}시간")
        if minutes:
            parts.append(f"{minutes}분")
        parts.append(f"{secs}초")
        return " ".join(parts)

    async def _confirm_and_run(
        self,
        interaction: discord.Interaction,
        *,
        message: str,
        action: Any,
        reason: str,
    ) -> None:
        await interaction.response.send_message(message)
        await asyncio.sleep(_CONFIRM_DELAY)
        await action(reason)

    async def _confirm_and_run_ctx(
        self,
        ctx: commands.Context,
        *,
        message: str,
        action: Any,
        reason: str,
    ) -> None:
        await ctx.send(message)
        await asyncio.sleep(_CONFIRM_DELAY)
        await action(reason)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    from core.container import container

    cog = AdminCog(bot, container.system_service)
    await bot.add_cog(cog)
