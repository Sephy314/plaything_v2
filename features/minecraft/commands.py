"""Minecraft feature commands — slash command group ``/마크``.

Provides full server lifecycle management: creation, start/stop, player
inspection, RCON commands (OP-gated), UUID registration (admin-only),
whitelist management and status queries.

Admin-only operations are gated behind Discord administrator permission.
RCON execution is gated behind Minecraft in-game OP permission instead.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext.commands import Bot

from core.exceptions import (
    MinecraftAliasExists,
    MinecraftBackupError,
    MinecraftBackupInProgress,
    MinecraftFolderError,
    MinecraftFolderNotFound,
    MinecraftPermissionError,
    MinecraftPortConflict,
    MinecraftProcessError,
    MinecraftRconError,
    MinecraftServerNotFound,
    MinecraftUnauthorized,
)
from core.logger import get_logger
from features.base import FeatureCog
from services.minecraft_backup_service import MinecraftBackupService
from services.minecraft_service import MinecraftService

log = get_logger(__name__)


# ------------------------------------------------------------------
# Autocomplete callbacks (module-level)
# ------------------------------------------------------------------


def _contains_match(text: str, current: str) -> bool:
    """Check if text matches current input (case-insensitive, partial match)."""
    current_lower = current.lower()
    text_lower = text.lower()

    # Exact prefix match
    if text_lower.startswith(current_lower):
        return True
    # Partial word match (for Korean names with spaces)
    if current_lower in text_lower:
        return True
    return False


async def server_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for server alias parameter."""
    try:
        from core.container import container, get_container

        # Handle both initialized and uninitialized container
        try:
            service = container.minecraft_service if container else None
        except Exception:
            service = None

        if not service:
            try:
                service = get_container().minecraft_service
            except RuntimeError:
                # Container not yet initialized
                return []

        servers = await service.list_servers()

        # Filter with both prefix and partial matching
        filtered = [s.alias for s in servers if _contains_match(s.alias, current)]

        # Create choices
        choices = []
        for alias in filtered[:25]:
            server = next((s for s in servers if s.alias == alias), None)
            if not server:
                continue

            from features.minecraft.models import STATUS_RUNNING

            status_str = "🟢 실행중" if server.status == STATUS_RUNNING else "🔴 정지"
            choices.append(
                app_commands.Choice(
                    name=f"{alias} {status_str}",
                    value=alias,
                )
            )

        return choices
    except Exception as e:
        log.error("server_autocomplete failed: %s", e, exc_info=True)
        return []


async def server_running_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for running server aliases only."""
    try:
        from core.container import container, get_container
        from features.minecraft.models import STATUS_RUNNING

        # Handle both initialized and uninitialized container
        try:
            service = container.minecraft_service if container else None
        except Exception:
            service = None

        if not service:
            try:
                service = get_container().minecraft_service
            except RuntimeError:
                return []

        servers = await service.list_servers()
        running = [s for s in servers if s.status == STATUS_RUNNING]

        # Filter by current input
        filtered = [s.alias for s in running if _contains_match(s.alias, current)]

        return [app_commands.Choice(name=alias, value=alias) for alias in filtered[:25]]
    except Exception as e:
        log.error("server_running_autocomplete failed: %s", e, exc_info=True)
        return []


async def scope_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for address scope (external/internal)."""
    scopes = [
        app_commands.Choice(name="external (외부/기본)", value="external"),
        app_commands.Choice(name="internal (내부)", value="internal"),
    ]

    if not current:
        return scopes

    current_lower = current.lower()
    return [
        s
        for s in scopes
        if s.name.lower().startswith(current_lower) or s.value.lower().startswith(current_lower)
    ]


async def whitelist_action_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for whitelist action (add/remove)."""
    actions = [
        app_commands.Choice(name="add (추가)", value="add"),
        app_commands.Choice(name="remove (제거)", value="remove"),
    ]

    if not current:
        return actions

    current_lower = current.lower()
    return [
        a
        for a in actions
        if a.name.lower().startswith(current_lower) or a.value.lower().startswith(current_lower)
    ]


async def whitelist_target_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for the whitelist target: Discord members.

    Suggests guild members matching the current input so admins can pick a
    member by nickname. Raw Minecraft nicknames are still accepted — they just
    don't match a member, so the input passes through unchanged.
    """
    guild = interaction.guild
    if guild is None or not guild.members:
        return []
    lowered = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for member in guild.members:
        candidates = [member.name, member.display_name]
        if member.nick:
            candidates.append(member.nick)
        if any(lowered in (n or "").lower() for n in candidates if n):
            choices.append(
                app_commands.Choice(
                    name=f"{member.display_name} (Discord 유저)",
                    value=member.name,
                )
            )
        if len(choices) >= 25:
            break
    return choices


async def external_folder_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for unmanaged external server folders under MC_PARENT_DIRECTORY."""
    try:
        from core.container import container, get_container

        try:
            service = container.minecraft_service if container else None
        except Exception:
            service = None
        if not service:
            try:
                service = get_container().minecraft_service
            except RuntimeError:
                return []

        names = await service.available_external_folders()
        filtered = [n for n in names if _contains_match(n, current)]
        return [app_commands.Choice(name=name, value=name) for name in filtered[:25]]
    except Exception as e:
        log.error("external_folder_autocomplete failed: %s", e, exc_info=True)
        return []


class MinecraftCog(FeatureCog):
    """Slash commands for Minecraft server management."""

    def __init__(
        self,
        bot: Bot,
        service: MinecraftService,
        backup_service: MinecraftBackupService,
    ) -> None:
        super().__init__(bot)
        self._service = service
        self._backup_service = backup_service

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
            await interaction.followup.send("범위는 `external` 또는 `internal`만 가능합니다.")
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
            await interaction.followup.send(
                "등록된 서버가 없습니다. `/마크_생성`으로 만들어 주세요."
            )
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

    @app_commands.command(
        name="마크_명령어", description="서버에 RCON 명령어를 실행합니다. (OP 권한 필요)"
    )
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
            f"UUID 등록 완료: <@{user.discord_id}> → `{user.minecraft_uuid}`\n"
            "등록된 모든 서버 화이트리스트에 자동 추가되었습니다."
        )

    @app_commands.command(
        name="마크_등록",
        description="자신의 Minecraft UUID를 등록해 모든 서버 화이트리스트에 자동 추가됩니다.",
    )
    @app_commands.describe(minecraft_uuid="Minecraft UUID")
    async def mc_register_self(
        self,
        interaction: discord.Interaction,
        minecraft_uuid: str,
    ) -> None:
        """Register the invoking member's own Minecraft UUID (self-service).

        Once registered the member is automatically whitelisted on every
        managed server, so any Discord server member is allowed by default.
        """
        await interaction.response.defer()
        try:
            user = await self._service.register_uuid(interaction.user.id, minecraft_uuid.strip())
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(
            f"UUID 등록 완료: <@{user.discord_id}> → `{user.minecraft_uuid}`\n"
            "등록된 모든 서버 화이트리스트에 자동 추가되었습니다."
        )

    # ------------------------------------------------------------------
    # Whitelist
    # ------------------------------------------------------------------

    @app_commands.command(name="마크_화이트리스트", description="화이트리스트를 관리합니다.")
    @app_commands.describe(
        alias="서버 별명",
        action="add (추가) 또는 remove (제거)",
        target="플레이어 닉네임 또는 Discord 멘션/닉네임",
    )
    @app_commands.autocomplete(alias=server_autocomplete)
    @app_commands.autocomplete(action=whitelist_action_autocomplete)
    @app_commands.autocomplete(target=whitelist_target_autocomplete)
    async def mc_whitelist(
        self,
        interaction: discord.Interaction,
        alias: str,
        action: Literal["add", "remove"],
        target: str,
    ) -> None:
        """Add or remove a player from the server's whitelist.

        ``target`` accepts either a raw Minecraft nickname or a Discord member
        (mention like ``<@1234>``, or a nickname/name). Discord members are
        resolved to their registered Minecraft UUID before the whitelist
        change is applied.
        """
        await interaction.response.defer()
        try:
            member = await self._resolve_member(interaction, target)
            if member is not None:
                output = await self._service.whitelist_user(alias.strip(), action, member.id)
                label = member.mention
            else:
                output = await self._service.whitelist(alias.strip(), action, target.strip())
                label = f"**{target.strip()}**"
        except MinecraftUnauthorized as exc:
            await interaction.followup.send(f"⚠️ {exc}")
            return
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        verb = "추가" if action == "add" else "제거"
        await interaction.followup.send(f"화이트리스트 {verb} 완료: {label}\n```{output[:500]}```")

    # ------------------------------------------------------------------
    # External server folder migration (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="마크_맵가져오기",
        description="외부 서버 폴더를 가져와 관리 서버로 등록합니다. (관리자)",
    )
    @app_commands.describe(
        alias="MC_PARENT_DIRECTORY 안에 있는 외부 서버 폴더명",
        port="포트 번호 (선택사항, 기본값: server.properties에서 읽음)",
    )
    @app_commands.autocomplete(alias=external_folder_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def mc_import_map(
        self,
        interaction: discord.Interaction,
        alias: str,
        port: int | None = None,
    ) -> None:
        """Register an existing external server folder as a managed server.

        The folder must already exist under ``MC_PARENT_DIRECTORY``. On
        success it is registered in PostgreSQL, and every registered
        Discord↔UUID member is applied to the folder's whitelist and OP list.
        """
        await interaction.response.defer()
        try:
            server = await self._service.migrate_external_folder(
                alias.strip(), port, interaction.user.id
            )
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(
            f"외부 서버 폴더 등록 완료: **{server.alias}** (포트 {server.port}, "
            f"폴더 `{server.folder_path}`)\n"
            "등록된 모든 유저가 화이트리스트/OP에 반영되었습니다."
        )

    # ------------------------------------------------------------------
    # World backup (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="마크_백업",
        description="서버 월드 백업을 생성합니다. (관리자)",
    )
    @app_commands.describe(alias="서버 별명")
    @app_commands.autocomplete(alias=server_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def mc_backup(
        self,
        interaction: discord.Interaction,
        alias: str,
    ) -> None:
        """Create a world backup for the named server."""
        await interaction.response.defer()
        try:
            path = await self._backup_service.create_backup(
                alias.strip(), created_by=interaction.user.id
            )
        except Exception as exc:
            await self._handle_error(interaction, exc)
            return
        await interaction.followup.send(f"백업 완료: `{path}`")

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

    @staticmethod
    async def _resolve_member(
        interaction: discord.Interaction,
        target: str,
    ) -> discord.Member | None:
        """Resolve a whitelist target string to a Discord member, if possible.

        Handles mentions (``<@id>`` / ``<@!id>``) and exact case-insensitive
        matches against a member's global name, display name or server
        nickname. Returns ``None`` when the input is not a known Discord
        member — callers then treat it as a raw Minecraft nickname.
        """
        stripped = target.strip()
        guild = interaction.guild
        if guild is None:
            return None

        mention = re.fullmatch(r"<@!?(\d+)>", stripped)
        if mention:
            member = guild.get_member(int(mention.group(1)))
            if member is None:
                try:
                    member = await guild.fetch_member(int(mention.group(1)))
                except (discord.NotFound, discord.HTTPException):
                    return None
            return member

        lowered = stripped.lower()
        for member in guild.members:
            candidates = [member.name, member.display_name]
            if member.nick:
                candidates.append(member.nick)
            if any((n or "").lower() == lowered for n in candidates):
                return member
        return None

    async def _handle_error(self, interaction: discord.Interaction, exc: Exception) -> None:
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
            MinecraftBackupInProgress: "이미 백업이 진행 중입니다. 잠시 후 다시 시도해 주세요.",
            MinecraftBackupError: "백업에 실패했습니다. 기존 백업은 유지됩니다.",
        }
        if isinstance(exc, (MinecraftPortConflict, MinecraftFolderNotFound)):
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

    cog = MinecraftCog(
        bot,
        container.minecraft_service,
        container.minecraft_backup_service,
    )
    await bot.add_cog(cog)
