"""Minecraft feature commands — prefix-command group ``!마크``.

Provides full server lifecycle management: creation, start/stop, player
inspection, RCON commands (OP-gated), UUID registration (admin-only),
whitelist management and status queries.

Admin-only operations are gated behind Discord administrator permission.
RCON execution is gated behind Minecraft in-game OP permission instead.
"""

from __future__ import annotations

from typing import Any

import discord
from discord.ext.commands import Bot, Context, group

from core.exceptions import (
    MinecraftAliasExists,
    MinecraftFolderError,
    MinecraftPermissionError,
    MinecraftPortConflict,
    MinecraftProcessError,
    MinecraftRconError,
    MinecraftServerNotFound,
    MinecraftUnauthorized,
)
from core.logger import get_logger
from features.base import FeatureCog
from services.minecraft_service import MinecraftService

log = get_logger(__name__)

USAGE = (
    "`!마크 생성 <맵이름> [포트]`\n"
    "`!마크 켜 <맵이름>`\n"
    "`!마크 꺼 <맵이름>`\n"
    "`!마크 유저확인 <맵이름>`\n"
    "`!마크 주소 <맵이름>`\n"
    "`!마크 명령어 <맵이름> <명령어>`\n"
    "`!마크 상태 <맵이름>`\n"
    "`!마크 서버`\n"
    "`!마크 로그 <맵이름>`\n"
    "`!마크 화이트리스트 추가/제거 <맵이름> <닉네임>`\n"
    "`!마크 UUID등록 <유저> <Minecraft UUID>` (관리자)"
)


class MinecraftCog(FeatureCog):
    """Prefix commands for Minecraft server management."""

    def __init__(self, bot: Bot, service: MinecraftService) -> None:
        super().__init__(bot)
        self._service = service

    # ------------------------------------------------------------------
    # Group root
    # ------------------------------------------------------------------

    @group(name="마크", invoke_without_command=True, help="Minecraft 서버 관리 명령어 그룹")
    async def minecraft(self, ctx: Context) -> None:
        """Show the available subcommands when no subcommand is given."""
        await ctx.send(USAGE)

    # ------------------------------------------------------------------
    # Server creation
    # ------------------------------------------------------------------

    @minecraft.command(name="생성", help="Minecraft 서버를 생성합니다.")
    async def mc_create(self, ctx: Context, alias: str, port: int = None) -> None:
        """Create a new Minecraft server with the given alias."""
        if not self._is_admin(ctx):
            await ctx.send("관리자 권한이 필요합니다.")
            return
        try:
            server = await self._service.register_server(alias.strip(), port, ctx.author.id)
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        await ctx.send(
            f"서버 생성 완료: **{server.alias}** (포트 {server.port}, 폴더 `{server.folder_path}`)"
        )

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    @minecraft.command(name="켜", help="Minecraft 서버를 시작합니다.")
    async def mc_start(self, ctx: Context, alias: str) -> None:
        """Start the named server."""
        try:
            server = await self._service.start(alias.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        await ctx.send(f"서버 시작: **{server.alias}** (포트 {server.port})")

    @minecraft.command(name="꺼", help="Minecraft 서버를 종료합니다.")
    async def mc_stop(self, ctx: Context, alias: str) -> None:
        """Stop the named server."""
        try:
            server = await self._service.stop(alias.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        await ctx.send(f"서버 종료: **{server.alias}**")

    # ------------------------------------------------------------------
    # Player / status
    # ------------------------------------------------------------------

    @minecraft.command(name="주소", help="서버 접속 주소를 출력합니다. [외부/내부, 기본 외부]")
    async def mc_address(self, ctx: Context, alias: str, scope: str = "외부") -> None:
        """Show the ``host:port`` connection address for the named server.

        Args:
            alias: The server alias.
            scope: ``외부`` (not same router, default) or ``내부`` (same router).
        """
        if scope not in ("외부", "내부"):
            await ctx.send("범위는 `외부` 또는 `내부`만 가능합니다. 예: `!마크 주소 survival 내부`")
            return
        internal = scope == "내부"
        try:
            address = await self._service.get_address(alias.strip(), internal=internal)
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        label = "내부(같은 공유기)" if internal else "외부(공유기 밖)"
        await ctx.send(f"**{alias}** ({label}) 접속 주소: `{address}`")

    @minecraft.command(name="유저확인", help="현재 접속 중인 플레이어를 확인합니다.")
    async def mc_players(self, ctx: Context, alias: str) -> None:
        """List the players currently online on the named server."""
        try:
            names, count = await self._service.get_players(alias.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        body = "\n".join(f"- {name}" for name in names) or "(없음)"
        await ctx.send(f"접속자 {count}명:\n{body}")

    @minecraft.command(name="상태", help="서버 상태를 확인합니다.")
    async def mc_status(self, ctx: Context, alias: str) -> None:
        """Show a concise runtime status snapshot for the named server."""
        try:
            info = await self._service.status(alias.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        await ctx.send(self._format_status(info))

    @minecraft.command(name="서버", help="모든 서버의 목록을 표시합니다.")
    async def mc_list_servers(self, ctx: Context) -> None:
        """List all managed Minecraft servers with their status."""
        try:
            servers = await self._service.list_servers()
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        
        if not servers:
            await ctx.send("등록된 서버가 없습니다. `!마크 생성`으로 만들어 주세요.")
            return
        
        lines = ["**등록된 서버 목록:**\n"]
        for server in servers:
            from features.minecraft.models import STATUS_RUNNING
            status_icon = "🟢" if server.status == STATUS_RUNNING else "🔴"
            lines.append(f"{status_icon} **{server.alias}** (포트 {server.port})")
        
        await ctx.send("\n".join(lines))

    @minecraft.command(name="로그", help="서버의 최근 로그를 표시합니다.")
    async def mc_logs(self, ctx: Context, alias: str) -> None:
        """Show the last 50 lines of the named server's latest.log file."""
        try:
            logs = await self._service.read_logs(alias.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        
        await ctx.send(f"**{alias}** 최근 로그:\n```\n{logs}\n```")

    # ------------------------------------------------------------------
    # RCON
    # ------------------------------------------------------------------

    @minecraft.command(name="명령어", help="서버에 RCON 명령어를 실행합니다. (OP 권한 필요)")
    async def mc_command(self, ctx: Context, alias: str, *, command: str) -> None:
        """Run an RCON command, gated by in-game OP permission."""
        try:
            output = await self._service.execute_command(alias.strip(), command, ctx.author.id)
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        await ctx.send(f"`{command}` 실행 결과:\n```{output[:1500] or '(결과 없음)'}```")

    # ------------------------------------------------------------------
    # UUID registration (admin-only)
    # ------------------------------------------------------------------

    @minecraft.command(name="UUID등록", help="Discord 유저와 Minecraft UUID를 연결합니다. (관리자)")
    async def mc_uuid(self, ctx: Context, target: discord.Member, minecraft_uuid: str) -> None:
        """Persist the Discord↔Minecraft UUID mapping for a member."""
        if not self._is_admin(ctx):
            await ctx.send("관리자 권한이 필요합니다.")
            return
        try:
            user = await self._service.register_uuid(target.id, minecraft_uuid.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        await ctx.send(f"UUID 등록 완료: <@{user.discord_id}> → `{user.minecraft_uuid}`")

    # ------------------------------------------------------------------
    # Whitelist
    # ------------------------------------------------------------------

    @minecraft.group(name="화이트리스트", invoke_without_command=True, help="화이트리스트 관리")
    async def mc_whitelist(self, ctx: Context) -> None:
        """Show usage when the whitelist group is invoked without a subcommand."""
        await ctx.send("`!마크 화이트리스트 추가/제거 <맵이름> <닉네임>`")

    @mc_whitelist.command(name="추가", help="화이트리스트에 플레이어를 추가합니다.")
    async def wl_add(self, ctx: Context, alias: str, nickname: str) -> None:
        """Add a nickname to the server's whitelist."""
        await self._wl_action(ctx, alias, "add", nickname)

    @mc_whitelist.command(name="제거", help="화이트리스트에서 플레이어를 제거합니다.")
    async def wl_remove(self, ctx: Context, alias: str, nickname: str) -> None:
        """Remove a nickname from the server's whitelist."""
        await self._wl_action(ctx, alias, "remove", nickname)

    async def _wl_action(self, ctx: Context, alias: str, action: str, nickname: str) -> None:
        try:
            output = await self._service.whitelist(alias.strip(), action, nickname.strip())
        except Exception as exc:
            await self._handle_error(ctx, exc)
            return
        verb = "추가" if action == "add" else "제거"
        await ctx.send(f"화이트리스트 {verb} 완료: **{nickname}**\n```{output[:500]}```")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_admin(ctx: Context) -> bool:
        if ctx.guild is None:
            return False
        if ctx.author == ctx.guild.owner:
            return True
        return bool(ctx.author.guild_permissions.administrator)

    @staticmethod
    def _format_status(info: dict[str, Any]) -> str:
        state = "🟢 실행 중" if info["running"] else "🔴 정지됨"
        return (
            f"**{info['alias']}** — {state}\n"
            f"포트: {info['port']} | 폴더: `{info['folder']}`\n"
            f"접속자: {info['player_count']}명 ({', '.join(info['online_players']) or '없음'})"
        )

    async def _handle_error(self, ctx: Context, exc: Exception) -> None:
        """Reply with a friendly, specific message for the exception type."""
        message = {
            MinecraftServerNotFound: "서버를 찾을 수 없습니다. "
            "먼저 `!마크 생성`으로 만들어 주세요.",
            MinecraftAliasExists: "이미 같은 이름의 서버가 존재합니다.",
            MinecraftFolderError: "서버 폴더를 준비하지 못했습니다.",
            MinecraftProcessError: "서버 프로세스 작업에 실패했습니다.",
            MinecraftRconError: "RCON 통신에 실패했습니다.",
            MinecraftPermissionError: "이 서버에서 OP 권한이 없습니다.",
            MinecraftUnauthorized: "권한이 없거나 입력이 올바르지 않습니다.",
        }
        if isinstance(exc, MinecraftPortConflict):
            log.warning("minecraft command rejected: %s", exc)
            await ctx.send(f"⚠️ {exc}")
            return
        # Most-specific match first
        for exc_type, text in message.items():
            if isinstance(exc, exc_type):
                log.warning("minecraft command rejected: %s", exc)
                await ctx.send(text)
                return
        log.error("minecraft command error: %s", exc, exc_info=exc)
        await ctx.send(f"오류가 발생했습니다: {exc}")


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    from core.container import container

    await bot.add_cog(MinecraftCog(bot, container.minecraft_service))
