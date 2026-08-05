"""Minecraft feature commands — slash command group ``/마크``.

Provides full server lifecycle management: creation, start/stop, player
inspection, RCON commands (OP-gated), UUID registration (admin-only),
whitelist management and status queries.

Admin-only operations are gated behind Discord administrator permission.
RCON execution is gated behind Minecraft in-game OP permission instead.
"""

from __future__ import annotations

from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext.commands import Bot

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


# ------------------------------------------------------------------
# Autocomplete callbacks (module-level)
# ------------------------------------------------------------------


async def server_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for server alias parameter."""
    try:
        from core.container import container
        servers = await container.minecraft_service.list_servers()
        aliases = [s.alias for s in servers]
        filtered = [a for a in aliases if a.lower().startswith(current.lower())]
        return [
            app_commands.Choice(name=alias, value=alias)
            for alias in filtered[:25]
        ]
    except Exception:
        return []


async def server_running_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for running server aliases only."""
    try:
        from core.container import container
        from features.minecraft.models import STATUS_RUNNING
        servers = await container.minecraft_service.list_servers()
        running = [s.alias for s in servers if s.status == STATUS_RUNNING]
        filtered = [a for a in running if a.lower().startswith(current.lower())]
        return [
            app_commands.Choice(name=alias, value=alias)
            for alias in filtered[:25]
        ]
    except Exception:
        return []


async def scope_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for address scope (external/internal)."""
    scopes = ["external", "internal"]
    filtered = [s for s in scopes if s.startswith(current.lower())]
    return [
        app_commands.Choice(name=scope, value=scope)
        for scope in filtered
    ]


async def whitelist_action_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for whitelist action (add/remove)."""
    actions = ["add", "remove"]
    filtered = [a for a in actions if a.startswith(current.lower())]
    return [
        app_commands.Choice(name=action, value=action)
        for action in filtered
    ]


class MinecraftCog(FeatureCog):
    """Slash commands for Minecraft server management."""

    def __init__(self, bot: Bot, service: MinecraftService) -> None:
        super().__init__(bot)
        self._service = service

    # ------------------------------------------------------------------
    # Server creation
    # ------------------------------------------------------------------

    @app_commands.command(name="마크_생성", description="Minecraft 서버를 생성합니다.")
    @app_commands.describe(alias="서버 별명", port="포트 번호 (선택사항)")
    @app_commands.default_permissions(administrator=True)
    async def mc_create(
        self,
        interaction: discord.Interaction,
        alias: str,
        port: int | None = None,
    ) -> None:
        """Create a new Minecraft server with the given alias."""
        await interaction.response.defer()
        try:
            server = await self._service.register_server(alias.strip(), port, interaction.user.id)
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(
            f"서버 생성 완료: **{server.alias}** (포트 {server.port}, 폴더 `{server.folder_path}`)"
        )

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    @app_commands.command(name="마크_켜", description="Minecraft 서버를 시작합니다.")
    @app_commands.describe(alias="서버 별명")
    @app_commands.autocomplete(alias=server_autocomplete)
    async def mc_start(self, interaction: discord.Interaction, alias: str) -> None:
        """Start the named server."""
        await interaction.response.defer()
        try:
            server = await self._service.start(alias.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(f"서버 시작: **{server.alias}** (포트 {server.port})")

    @app_commands.command(name="마크_꺼", description="Minecraft 서버를 종료합니다.")
    @app_commands.describe(alias="서버 별명")
    @app_commands.autocomplete(alias=server_running_autocomplete)
    async def mc_stop(self, interaction: discord.Interaction, alias: str) -> None:
        """Stop the named server."""
        await interaction.response.defer()
        try:
            server = await self._service.stop(alias.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(f"서버 종료: **{server.alias}**")

    # ------------------------------------------------------------------
    # Player / status
    # ------------------------------------------------------------------

    @app_commands.command(name="마크_주소", description="서버 접속 주소를 출력합니다.")
    @app_commands.describe(
        alias="서버 별명",
        scope="external (외부/기본) 또는 internal (내부)",
    )
    @app_commands.autocomplete(alias=server_autocomplete)
    @app_commands.autocomplete(scope=scope_autocomplete)
    async def mc_address(
        self,
        interaction: discord.Interaction,
        alias: str,
        scope: str = "external",
    ) -> None:
        """Show the ``host:port`` connection address for the named server.

        Args:
            alias: The server alias.
            scope: ``external`` (not same router, default) or ``internal`` (same router).
        """
        await interaction.response.defer()
        if scope not in ("external", "internal"):
            await interaction.followup.send(
                "범위는 `external` 또는 `internal`만 가능합니다."
            )
            return
        internal = scope == "internal"
        try:
            address = await self._service.get_address(alias.strip(), internal=internal)
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        label = "내부(같은 공유기)" if internal else "외부(공유기 밖)"
        await interaction.followup.send(f"**{alias}** ({label}) 접속 주소: `{address}`")

    @app_commands.command(name="마크_유저확인", description="현재 접속 중인 플레이어를 확인합니다.")
    @app_commands.describe(alias="서버 별명")
    @app_commands.autocomplete(alias=server_running_autocomplete)
    async def mc_players(self, interaction: discord.Interaction, alias: str) -> None:
        """List the players currently online on the named server."""
        await interaction.response.defer()
        try:
            names, count = await self._service.get_players(alias.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        body = "\n".join(f"- {name}" for name in names) or "(없음)"
        await interaction.followup.send(f"접속자 {count}명:\n{body}")

    @app_commands.command(name="마크_상태", description="서버 상태를 확인합니다.")
    @app_commands.describe(alias="서버 별명")
    @app_commands.autocomplete(alias=server_autocomplete)
    async def mc_status(self, interaction: discord.Interaction, alias: str) -> None:
        """Show a concise runtime status snapshot for the named server."""
        await interaction.response.defer()
        try:
            info = await self._service.status(alias.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(self._format_status(info))

    @app_commands.command(name="마크_서버", description="모든 서버의 목록을 표시합니다.")
    async def mc_list_servers(self, interaction: discord.Interaction) -> None:
        """List all managed Minecraft servers with their status."""
        await interaction.response.defer()
        try:
            servers = await self._service.list_servers()
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return

        if not servers:
            await interaction.followup.send("등록된 서버가 없습니다. `/마크_생성`으로 만들어 주세요.")
            return

        from features.minecraft.models import STATUS_RUNNING
        lines = ["**등록된 서버 목록:**\n"]
        for server in servers:
            status_icon = "🟢" if server.status == STATUS_RUNNING else "🔴"
            lines.append(f"{status_icon} **{server.alias}** (포트 {server.port})")

        await interaction.followup.send("\n".join(lines))

    @app_commands.command(name="마크_로그", description="서버의 최근 로그를 표시합니다.")
    @app_commands.describe(alias="서버 별명")
    @app_commands.autocomplete(alias=server_autocomplete)
    async def mc_logs(self, interaction: discord.Interaction, alias: str) -> None:
        """Show the last 50 lines of the named server's latest.log file."""
        await interaction.response.defer()
        try:
            logs = await self._service.read_logs(alias.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return

        await interaction.followup.send(f"**{alias}** 최근 로그:\n```\n{logs}\n```")

    # ------------------------------------------------------------------
    # RCON
    # ------------------------------------------------------------------

    @app_commands.command(name="마크_명령어", description="서버에 RCON 명령어를 실행합니다. (OP 권한 필요)")
    @app_commands.describe(alias="서버 별명", command="실행할 명령어")
    @app_commands.autocomplete(alias=server_running_autocomplete)
    async def mc_command(
        self,
        interaction: discord.Interaction,
        alias: str,
        command: str,
    ) -> None:
        """Run an RCON command, gated by in-game OP permission."""
        await interaction.response.defer()
        try:
            output = await self._service.execute_command(
                alias.strip(), command, interaction.user.id
            )
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(
            f"`{command}` 실행 결과:\n```{output[:1500] or '(결과 없음)'}```"
        )

    # ------------------------------------------------------------------
    # UUID registration (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="마크_uuid등록",
        description="Discord 유저와 Minecraft UUID를 연결합니다. (관리자)",
    )
    @app_commands.describe(target="대상 유저", minecraft_uuid="Minecraft UUID")
    @app_commands.default_permissions(administrator=True)
    async def mc_uuid(
        self,
        interaction: discord.Interaction,
        target: discord.User,
        minecraft_uuid: str,
    ) -> None:
        """Persist the Discord↔Minecraft UUID mapping for a member."""
        await interaction.response.defer()
        try:
            user = await self._service.register_uuid(target.id, minecraft_uuid.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(
            f"UUID 등록 완료: <@{user.discord_id}> → `{user.minecraft_uuid}`"
        )

    # ------------------------------------------------------------------
    # Whitelist
    # ------------------------------------------------------------------

    @app_commands.command(name="마크_화이트리스트", description="화이트리스트를 관리합니다.")
    @app_commands.describe(
        alias="서버 별명",
        action="add (추가) 또는 remove (제거)",
        nickname="플레이어 닉네임",
    )
    @app_commands.autocomplete(alias=server_autocomplete)
    @app_commands.autocomplete(action=whitelist_action_autocomplete)
    async def mc_whitelist(
        self,
        interaction: discord.Interaction,
        alias: str,
        action: Literal["add", "remove"],
        nickname: str,
    ) -> None:
        """Add or remove a nickname from the server's whitelist."""
        await interaction.response.defer()
        try:
            output = await self._service.whitelist(alias.strip(), action, nickname.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        verb = "추가" if action == "add" else "제거"
        await interaction.followup.send(f"화이트리스트 {verb} 완료: **{nickname}**\n```{output[:500]}```")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_status(info: dict[str, Any]) -> str:
        state = "🟢 실행 중" if info["running"] else "🔴 정지됨"
        return (
            f"**{info['alias']}** — {state}\n"
            f"포트: {info['port']} | 폴더: `{info['folder']}`\n"
            f"접속자: {info['player_count']}명 ({', '.join(info['online_players']) or '없음'})"
        )

    async def _handle_error(
        self, interaction: discord.Interaction, exc: Exception
    ) -> None:
        """Reply with a friendly, specific message for the exception type."""
        message = {
            MinecraftServerNotFound: "서버를 찾을 수 없습니다. "
            "먼저 `/마크_생성`으로 만들어 주세요.",
            MinecraftAliasExists: "이미 같은 이름의 서버가 존재합니다.",
            MinecraftFolderError: "서버 폴더를 준비하지 못했습니다.",
            MinecraftProcessError: "서버 프로세스 작업에 실패했습니다.",
            MinecraftRconError: "RCON 통신에 실패했습니다.",
            MinecraftPermissionError: "이 서버에서 OP 권한이 없습니다.",
            MinecraftUnauthorized: "권한이 없거나 입력이 올바르지 않습니다.",
        }
        if isinstance(exc, MinecraftPortConflict):
            log.warning("minecraft command rejected: %s", exc)
            await interaction.followup.send(f"⚠️ {exc}")
            return
        # Most-specific match first
        for exc_type, text in message.items():
            if isinstance(exc, exc_type):
                log.warning("minecraft command rejected: %s", exc)
                await interaction.followup.send(text)
                return
        log.error("minecraft command error: %s", exc, exc_info=exc)
        await interaction.followup.send(f"오류가 발생했습니다: {exc}")


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    from core.container import container

    cog = MinecraftCog(bot, container.minecraft_service)
    await bot.add_cog(cog)
