"""Minecraft service — business logic for server management.

Owns the full lifecycle of a managed server: atomic creation, process
start/stop via subprocess, RCON interaction, OP-based permission checks,
whitelist control, player tracking and the auto-shutdown policy.

Each running server gets a background monitor task that polls the player
count over RCON and drives the idle-shutdown timer.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import uuid as uuid_lib
from pathlib import Path
from typing import Any

import aiohttp

from config.settings import Settings
from core.exceptions import (
    MinecraftAliasExists,
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
from features.minecraft.models import STATUS_RUNNING, STATUS_STOPPED, MinecraftServer, MinecraftUser
from features.minecraft.rcon import (
    RCONAuthError,
    RCONClient,
    RCONConnectionError,
    RCONError,
)
from repository.minecraft_repository import MinecraftRepository

log = get_logger(__name__)

ALIAS_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")
MIN_PORT = 1024
MAX_PORT = 65535
DEFAULT_PORT = 25565
DEFAULT_RCON_PORT = 25575
_USER_AGENT = "plaything-v2-bot/0.1 (https://github.com/anomalyco/plaything_v2)"

_PROPERTIES_KEYS = (
    "server-port",
    "enable-rcon",
    "rcon.port",
    "rcon.password",
    "white-list",
    "enforce-whitelist",
    "motd",
    "max-players",
    "gamemode",
    "level-type",
    "online-mode",
)


class MinecraftService:
    """Handles Minecraft server business rules."""

    def __init__(self, minecraft_repository: MinecraftRepository, settings: Settings) -> None:
        self._user_agent = _USER_AGENT
        self._repository = minecraft_repository
        self._settings = settings
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._monitors: dict[int, asyncio.Task] = {}
        self._shutdown_timers: dict[int, asyncio.Task] = {}
        self._stop_grace = 15.0
        self._stop_kill_grace = 5.0

    # ------------------------------------------------------------------
    # Public API used by the command layer
    # ------------------------------------------------------------------

    async def list_servers(self) -> list[MinecraftServer]:
        """Return all managed servers."""
        return await self._repository.list_servers()

    async def find_server(self, alias: str) -> MinecraftServer:
        """Fetch a server by alias, raising if it does not exist."""
        server = await self._repository.find_server_by_alias(alias)
        if server is None:
            raise MinecraftServerNotFound(f"Server '{alias}' not found")
        return server

    async def register_server(
        self, alias: str, port: int | None, created_by: int
    ) -> MinecraftServer:
        """Atomically create a Minecraft server.

        Performs, in order: alias validation, port selection, folder and file
        creation, ``server.properties`` / whitelist setup, DB insert and creator
        OP registration. On any failure the created folder is removed and the
        DB insert is rolled back.

        Args:
            alias: Unique server alias.
            port: Requested port, or ``None`` for auto-discovery.
            created_by: Discord id of the creating user.
        """
        self._validate_alias(alias)

        existing = await self._repository.find_server_by_alias(alias)
        if existing is not None:
            raise MinecraftAliasExists(f"Alias '{alias}' is already in use")

        if port is None:
            port = await self._find_free_port()
        else:
            self._validate_port(port)
            start, end = self._port_range()
            if not start <= port <= end:
                available = await self._first_available_port()
                raise MinecraftPortConflict(
                    f"Port {port} is outside the published range {start}-{end}. "
                    f"A free port is available: {available}"
                )
            conflict = await self._repository.find_server_by_port(port)
            if conflict is not None:
                available = await self._first_available_port()
                raise MinecraftPortConflict(
                    f"Port {port} is already in use by another server. "
                    f"A free port is available: {available}"
                )
            if not self._is_port_free(port):
                available = await self._first_available_port()
                raise MinecraftPortConflict(
                    f"Port {port} is not free on the host (already bound). "
                    f"A free port is available: {available}"
                )

        parent = self._parent_directory()
        folder = parent / alias
        try:
            self._prepare_folder(folder, alias, port)
        except MinecraftFolderError:
            raise
        except Exception as exc:
            raise MinecraftFolderError(f"Failed to prepare folder for '{alias}': {exc}") from exc

        server: MinecraftServer | None = None
        try:
            server = await self._repository.register_server(alias, str(folder), port, created_by)
        except Exception as exc:
            self._remove_folder(folder)
            log.error("failed to register server %s (rolled back): %s", alias, exc, exc_info=exc)
            raise

        await self._register_creator_op(server, created_by)
        await self._sync_all_whitelists(server)
        log.info(
            "Minecraft server created: alias=%s port=%s folder=%s",
            server.alias,
            server.port,
            server.folder_path,
        )
        return server

    async def migrate_external_folder(
        self, alias: str, port: int | None, created_by: int
    ) -> MinecraftServer:
        """Import an existing external server folder as a managed server.

        Unlike :meth:`register_server` this does *not* create a new folder —
        the folder must already exist on disk (e.g. a map/server folder
        dropped into ``MC_PARENT_DIRECTORY``). The folder is registered in
        PostgreSQL and every registered Discord↔UUID member is applied to the
        folder's whitelist and OP list so the existing whitelist / admin
        configuration carries over to the migrated server.

        Args:
            alias: Unique server alias (must equal the folder name under the
                parent directory).
            port: Requested port, or ``None`` to read it from the folder's
                ``server.properties`` (falling back to auto-discovery).
            created_by: Discord id of the migrating user.

        Raises:
            MinecraftAliasExists: If the alias or folder is already managed.
            MinecraftFolderNotFound: If no matching folder exists on disk.
            MinecraftPortConflict: If the resolved port is out of range or
                already assigned to another managed server.
        """
        self._validate_alias(alias)

        existing = await self._repository.find_server_by_alias(alias)
        if existing is not None:
            raise MinecraftAliasExists(f"Alias '{alias}' is already in use")

        parent = self._parent_directory()
        folder = parent / alias
        if not folder.is_dir():
            raise MinecraftFolderNotFound(
                f"외부 서버 폴더를 찾을 수 없습니다: {folder} "
                "(폴더를 MC_PARENT_DIRECTORY 아래에 두어야 합니다)"
            )

        existing_by_folder = await self._repository.find_server_by_folder(str(folder))
        if existing_by_folder is not None:
            raise MinecraftAliasExists(
                f"이미 등록된 폴더입니다: {folder} (서버 '{existing_by_folder.alias}')"
            )

        # Resolve the port: explicit argument > folder's server.properties > free port.
        if port is None:
            port = self._read_server_port(folder)
        if port is None:
            port = await self._find_free_port()
        else:
            self._validate_port(port)
            start, end = self._port_range()
            if not start <= port <= end:
                raise MinecraftPortConflict(
                    f"Port {port} is outside the published range {start}-{end}"
                )
            conflict = await self._repository.find_server_by_port(port)
            if conflict is not None:
                raise MinecraftPortConflict(f"Port {port} is already in use by another server")

        server = await self._repository.register_server(alias, str(folder), port, created_by)
        try:
            self._patch_external_properties(folder, alias)
            await self._sync_whitelist_and_ops(server)
        except Exception as exc:  # pragma: no cover - defensive
            # The row is already registered; log the post-setup failure instead
            # of rolling the import back entirely.
            log.warning("post-migration setup failed for %s: %s", alias, exc, exc_info=exc)

        log.info(
            "External server folder migrated: alias=%s port=%s folder=%s created_by=%s",
            server.alias,
            server.port,
            server.folder_path,
            created_by,
        )
        return server

    async def available_external_folders(self) -> list[str]:
        """Return folder names under ``MC_PARENT_DIRECTORY`` not yet managed.

        Used by the command-layer autocomplete so admins can pick which
        external folder to migrate.
        """
        try:
            parent = self._parent_directory()
        except MinecraftFolderError:
            return []
        if not parent.is_dir():
            return []
        registered = {s.folder_path for s in await self._repository.list_servers()}
        names = []
        for entry in sorted(parent.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and str(entry) not in registered:
                names.append(entry.name)
        return names

    async def start(self, alias: str) -> MinecraftServer:
        """Start a server by alias."""
        server = await self.find_server(alias)
        if server.id in self._processes:
            raise MinecraftProcessError(f"Server '{alias}' is already running")
        self._validate_server_port(server.port)

        folder = Path(server.folder_path)
        try:
            await self._ensure_runtime_files(server, folder)
        except MinecraftProcessError:
            raise
        except Exception as exc:
            raise MinecraftProcessError(f"Failed to prepare runtime for '{alias}': {exc}") from exc

        java = self._settings.mc_java_command
        args = [
            java,
            f"-Xmx{self._settings.mc_max_memory}",
            "-jar",
            self._jar_name(),
            "nogui",
        ]

        folder.mkdir(parents=True, exist_ok=True)
        log_handle = open(folder / "latest.log", "ab")
        err_handle = open(folder / "latest.err.log", "ab")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(folder),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_handle,
                stderr=err_handle,
            )
        except (TimeoutError, OSError) as exc:
            log_handle.close()
            err_handle.close()
            raise MinecraftProcessError(f"Failed to launch server '{alias}': {exc}") from exc

        self._processes[server.id] = proc
        await self._repository.set_status(server.id, STATUS_RUNNING)
        self._monitors[server.id] = asyncio.create_task(
            self._monitor_loop(server), name=f"mc-monitor-{server.id}"
        )
        log.info(
            "Minecraft server started: alias=%s pid=%s port=%s",
            server.alias,
            proc.pid,
            server.port,
        )
        return await self._repository.find_server_by_alias(alias) or server

    async def stop(self, alias: str) -> MinecraftServer:
        """Stop a server by alias (via RCON ``stop``)."""
        server = await self.find_server(alias)
        await self._stop_server(server)
        return server

    async def status(self, alias: str) -> dict[str, Any]:
        """Return a snapshot of a server's runtime status."""
        server = await self.find_server(alias)
        running = server.id in self._processes
        names, count = [], 0
        if running:
            try:
                names, count = await self._list_players(server)
            except MinecraftRconError:
                names, count = [], 0
        return {
            "alias": server.alias,
            "running": running,
            "status": server.status,
            "port": server.port,
            "folder": server.folder_path,
            "online_players": names,
            "player_count": count,
        }

    async def get_address(self, alias: str, internal: bool = False) -> str:
        """Return the connection address ``host:port`` for a server.

        Args:
            alias: The server alias.
            internal: If True, use the same-router address (LAN IP);
                otherwise the public host from ``MC_PUBLIC_HOST``.

        The external host comes from ``MC_PUBLIC_HOST`` when configured. If it
        is empty, the same LAN host fallback is used so the command still
        returns a usable address on local networks.

        The internal host comes from ``MC_INTERNAL_HOST`` or is auto-detected;
        inside Docker the container IP is not the host LAN IP, so configure
        ``MC_INTERNAL_HOST`` for the same-router case.
        """
        server = await self.find_server(alias)
        if internal:
            return f"{self._lan_host()}:{server.port}"
        host = self._settings.mc_public_host.strip() or self._lan_host()
        if not host:
            return f"<IP>:{server.port} (접속 호스트를 찾지 못했습니다)"
        return f"{host}:{server.port}"

    def _lan_host(self) -> str:
        """Return the host LAN IP for same-router access.

        Prefers ``MC_INTERNAL_HOST``; otherwise tries to detect the outgoing
        interface IP, falling back to ``localhost``.
        """
        configured = self._settings.mc_internal_host.strip()
        if configured:
            return configured
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return "localhost"

    async def get_players(self, alias: str) -> tuple[list[str], int]:
        """Return ``(player_names, count)`` for a running server."""
        server = await self.find_server(alias)
        if server.id not in self._processes:
            raise MinecraftProcessError(f"Server '{alias}' is not running")
        return await self._list_players(server)

    async def execute_command(self, alias: str, command: str, discord_id: int) -> str:
        """Run an RCON command on a server, after verifying OP permission.

        Discord administrator permission is *not* sufficient — the invoking
        user must be OP inside the Minecraft server itself.

        Args:
            alias: The server alias.
            command: The command string.
            discord_id: The invoking Discord user id.

        Returns:
            The command's console output.
        """
        server = await self.find_server(alias)
        if server.id not in self._processes:
            raise MinecraftProcessError(f"Server '{alias}' is not running")
        if not await self._is_op(server, discord_id):
            log.warning("Unauthorized RCON attempt: alias=%s discord_id=%s", alias, discord_id)
            raise MinecraftPermissionError("You are not OP on this Minecraft server")
        output = await self._rcon_exec(server, command)
        log.info("RCON executed: alias=%s discord_id=%s command=%s", alias, discord_id, command)
        return output

    async def register_uuid(self, discord_id: int, minecraft_uuid: str) -> MinecraftUser:
        """Persist the Discord↔UUID mapping for a user.

        Once registered, the user is automatically added to the whitelist of
        every managed server, so DB-registered members are allowed by default.
        """
        normalized = self._canonical_uuid(minecraft_uuid)
        if normalized is None:
            raise MinecraftUnauthorized(f"Invalid Minecraft UUID: {minecraft_uuid!r}")
        user = await self._repository.upsert_user(discord_id, normalized)
        await self._sync_user_to_whitelists(user)
        log.info("Minecraft UUID registered: discord_id=%s uuid=%s", discord_id, normalized)
        return user

    async def find_uuid(self, discord_id: int) -> str | None:
        """Return the stored Minecraft UUID for a Discord user, if any."""
        user = await self._repository.find_user_by_discord(discord_id)
        return user.minecraft_uuid if user else None

    async def whitelist(self, alias: str, action: str, nickname: str) -> str:
        """Add or remove a nickname from a server's whitelist via RCON.

        Args:
            alias: The server alias.
            action: ``"add"`` or ``"remove"``.
            nickname: The Minecraft username.
        """
        server = await self.find_server(alias)
        if server.id not in self._processes:
            raise MinecraftProcessError(f"Server '{alias}' is not running")
        verb = "add" if action == "add" else "remove"
        output = await self._rcon_exec(server, f"whitelist {verb} {nickname}")
        log.info("Whitelist updated: alias=%s action=%s nickname=%s", alias, action, nickname)
        return output

    async def is_running(self, alias: str) -> bool:
        """Return whether a server process is currently managed/running."""
        server = await self.find_server(alias)
        return server.id in self._processes

    async def send_rcon(self, alias: str, command: str) -> str:
        """Execute an RCON command with no permission checks (internal use).

        Used by internal subsystems such as the world backup service to run
        ``save-off`` / ``save-all flush`` / ``save-on``.

        Args:
            alias: The server alias.
            command: The command string.

        Returns:
            The command's console output.
        """
        server = await self.find_server(alias)
        return await self._rcon_exec(server, command)

    async def whitelist_user(self, alias: str, action: str, discord_id: int) -> str:
        """Add/remove a Discord member from a server's whitelist via their UUID.

        The member must have a registered Discord↔UUID mapping (see
        :meth:`register_uuid`). When the server is running the change is
        applied over RCON so it takes effect immediately; otherwise the
        folder's ``whitelist.json`` is edited directly and applies on next
        start.

        Args:
            alias: The server alias.
            action: ``"add"`` or ``"remove"``.
            discord_id: The Discord user id of the member.

        Raises:
            MinecraftUnauthorized: If the Discord user has no registered UUID.
        """
        user = await self._repository.find_user_by_discord(discord_id)
        if user is None:
            raise MinecraftUnauthorized(
                f"<@{discord_id}> 님은 아직 Minecraft UUID를 등록하지 않았습니다. "
                "`/마크_등록 <uuid>`으로 먼저 등록해 주세요."
            )
        server = await self.find_server(alias)
        verb = "add" if action == "add" else "remove"
        folder = Path(server.folder_path)
        if server.id in self._processes:
            output = await self._rcon_exec(server, f"whitelist {verb} {user.minecraft_uuid}")
            log.info(
                "Whitelist updated (discord): alias=%s action=%s discord_id=%s uuid=%s",
                alias,
                action,
                discord_id,
                user.minecraft_uuid,
            )
            return output

        if action == "add":
            added = self._ensure_whitelist_entry(folder, user.minecraft_uuid)
            detail = "added" if added else "already present"
        else:
            removed = self._remove_whitelist_entry(folder, user.minecraft_uuid)
            detail = "removed" if removed else "not present"
        log.info(
            "Whitelist updated (file): alias=%s action=%s discord_id=%s (%s)",
            alias,
            action,
            discord_id,
            detail,
        )
        return f"whitelist {verb} {user.minecraft_uuid}: {detail}"

    async def whitelist_all_users(self, alias: str) -> int:
        """Whitelist every DB-registered user on an existing server.

        Useful for existing maps/servers that were set up before automatic
        whitelisting was introduced, or to re-apply the full member list.
        Users already on the whitelist are skipped. Running servers get each
        missing user applied over RCON immediately; stopped servers get
        ``whitelist.json`` updated directly.

        Args:
            alias: The server alias.

        Returns:
            The number of newly applied users.
        """
        server = await self.find_server(alias)
        users = await self._repository.list_users()
        folder = Path(server.folder_path)
        existing = {
            self._normalize_uuid(e.get("uuid"))
            for e in _load_json_list(folder / "whitelist.json")
            if isinstance(e, dict) and self._normalize_uuid(e.get("uuid"))
        }
        missing = [
            user for user in users if self._normalize_uuid(user.minecraft_uuid) not in existing
        ]
        if not missing:
            log.info(
                "bulk whitelist already complete: alias=%s total=%s",
                alias,
                len(users),
            )
            return 0

        applied = 0
        if server.id in self._processes:
            for user in missing:
                try:
                    await self._rcon_exec(server, f"whitelist add {user.minecraft_uuid}")
                    applied += 1
                except MinecraftRconError:
                    log.warning(
                        "RCON whitelist add failed for %s (uuid=%s); skipping",
                        server.alias,
                        user.minecraft_uuid,
                    )
        else:
            for user in missing:
                if self._ensure_whitelist_entry(folder, user.minecraft_uuid):
                    applied += 1
        log.info(
            "bulk whitelist applied: alias=%s total=%s missing=%s applied=%s",
            alias,
            len(users),
            len(missing),
            applied,
        )
        return applied

    async def read_logs(self, alias: str, lines: int = 50) -> str:
        """Read the last N lines of the server's latest.log file."""
        server = await self.find_server(alias)
        log_path = Path(server.folder_path) / "latest.log"
        if not log_path.exists():
            return "(로그 파일이 없습니다)"
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
            log_lines = content.splitlines()
            # Return last N lines, ensuring we don't exceed Discord's message limit (~2000 chars)
            tail = log_lines[-lines:] if len(log_lines) > lines else log_lines
            result = "\n".join(tail)
            # Truncate if still too large
            if len(result) > 1900:
                result = "..." + result[-1900:]
            return result or "(로그 파일이 비어있습니다)"
        except OSError as exc:
            raise MinecraftFolderError(f"로그 파일을 읽을 수 없습니다: {exc}") from exc

    async def shutdown_all(self) -> None:
        """Gracefully stop every running server (bot shutdown hook)."""
        running = await self._repository.list_running_servers()
        for server in running:
            try:
                await self._stop_server(server)
            except Exception as exc:  # pragma: no cover - defensive
                log.error("failed to stop %s during shutdown: %s", server.alias, exc, exc_info=exc)

    # ------------------------------------------------------------------
    # Creation internals
    # ------------------------------------------------------------------

    def _parent_directory(self) -> Path:
        raw = self._settings.mc_parent_directory
        if not raw.strip():
            raise MinecraftFolderError("MC_PARENT_DIRECTORY is not configured")
        parent = Path(raw).expanduser()
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MinecraftFolderError(f"Cannot create parent directory {parent}: {exc}") from exc
        return parent

    @staticmethod
    def _validate_alias(alias: str) -> None:
        if not ALIAS_PATTERN.match(alias):
            raise MinecraftUnauthorized("Alias must be 1-50 chars of letters, digits, '-' or '_'")

    @staticmethod
    def _validate_port(port: int) -> None:
        if not MIN_PORT <= port <= MAX_PORT:
            raise MinecraftUnauthorized(f"Port must be between {MIN_PORT} and {MAX_PORT}")

    def _validate_server_port(self, port: int) -> None:
        """Validate that a port falls inside the published, reachable range."""
        start, end = self._port_range()
        if not start <= port <= end:
            raise MinecraftPortConflict(
                f"Port {port} is outside the published range {start}-{end} "
                "(MC_PORT_START/MC_PORT_END)"
            )

    def _port_range(self) -> tuple[int, int]:
        start = self._settings.mc_port_start
        end = max(self._settings.mc_port_end, start)
        return start, end

    def _prepare_folder(self, folder: Path, alias: str, port: int) -> None:
        """Create folder and write the static configuration files."""
        if folder.exists():
            raise MinecraftFolderError(f"Folder already exists: {folder}")
        try:
            folder.mkdir(parents=True)
        except OSError as exc:
            raise MinecraftFolderError(f"Cannot create folder {folder}: {exc}") from exc

        try:
            (folder / "eula.txt").write_text("eula=true\n", encoding="utf-8")
            (folder / "ops.json").write_text("[]\n", encoding="utf-8")
            (folder / "whitelist.json").write_text("[]\n", encoding="utf-8")
            self._write_server_properties(folder, alias, port)
        except OSError as exc:
            self._remove_folder(folder)
            raise MinecraftFolderError(f"Failed to write server files in {folder}: {exc}") from exc

    def _write_server_properties(self, folder: Path, alias: str, port: int) -> None:
        rcon_port = self._rcon_port_for_port(port)
        password = self._rcon_password_for_alias(alias)
        values = {
            "server-port": str(port),
            "enable-rcon": "true",
            "rcon.port": str(rcon_port),
            "rcon.password": password,
            "white-list": "true",
            "enforce-whitelist": "true",
            "motd": alias,
            "max-players": "20",
            "gamemode": "survival",
            "level-type": "default",
            "online-mode": "true",
        }
        lines = [f"{key}={values[key]}\n" for key in _PROPERTIES_KEYS]
        (folder / "server.properties").write_text("".join(lines), encoding="utf-8")
        (folder / ".rcon_password").write_text(password, encoding="utf-8")

    def _ensure_static_files(self, folder: Path, alias: str, port: int) -> None:
        """Create missing bootstrap files without overwriting existing data."""
        eula = folder / "eula.txt"
        if not eula.exists():
            eula.write_text("eula=true\n", encoding="utf-8")
        ops = folder / "ops.json"
        if not ops.exists():
            ops.write_text("[]\n", encoding="utf-8")
        whitelist = folder / "whitelist.json"
        if not whitelist.exists():
            whitelist.write_text("[]\n", encoding="utf-8")
        props = folder / "server.properties"
        if not props.exists():
            self._write_server_properties(folder, alias, port)

    @staticmethod
    def _ensure_paper_global_config(folder: Path) -> None:
        """Disable Paper's spark profiler to avoid JVM crashes on this platform."""
        config_dir = folder / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / "paper-global.yml"
        block = "spark:\n" "  enable-immediately: false\n" "  enabled: false\n"
        if not path.exists():
            path.write_text(block, encoding="utf-8")
            return

        lines = path.read_text(encoding="utf-8").splitlines()
        output: list[str] = []
        in_spark = False
        saw_spark = False
        saw_enabled = False
        saw_enable_immediately = False

        for line in lines:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped.startswith("spark:") and indent == 0:
                in_spark = True
                saw_spark = True
                output.append("spark:")
                continue
            if in_spark:
                if indent == 0 and stripped.endswith(":") and not stripped.startswith("-"):
                    if not saw_enable_immediately:
                        output.append("  enable-immediately: false")
                    if not saw_enabled:
                        output.append("  enabled: false")
                    in_spark = False
                    output.append(line)
                    continue
                key = stripped.split(":", 1)[0]
                if key == "enabled":
                    output.append("  enabled: false")
                    saw_enabled = True
                    continue
                if key == "enable-immediately":
                    output.append("  enable-immediately: false")
                    saw_enable_immediately = True
                    continue
            output.append(line)

        if in_spark:
            if not saw_enable_immediately:
                output.append("  enable-immediately: false")
            if not saw_enabled:
                output.append("  enabled: false")

        if not saw_spark:
            if output and output[-1].strip():
                output.append("")
            output.extend(block.rstrip("\n").splitlines())

        path.write_text("\n".join(output) + "\n", encoding="utf-8")

    async def _register_creator_op(self, server: MinecraftServer, created_by: int) -> None:
        """Register the creator as OP on the new server, if they have a UUID."""
        user = await self._repository.find_user_by_discord(created_by)
        if user is None:
            log.warning("Creator has no Minecraft UUID; skipping OP registration")
            return
        folder = Path(server.folder_path)
        self._add_to_ops(folder, user.minecraft_uuid)
        self._add_to_whitelist(folder, user.minecraft_uuid)
        log.info("Creator registered as OP: alias=%s uuid=%s", server.alias, user.minecraft_uuid)

    async def _sync_all_whitelists(self, server: MinecraftServer) -> None:
        """Whitelist every registered member on a newly created server.

        Only the creator is granted OP (see :meth:`_register_creator_op`); all
        other DB-registered members are whitelisted so they can join by
        default. Entries already present are left untouched.
        """
        users = await self._repository.list_users()
        folder = Path(server.folder_path)
        added = 0
        for user in users:
            if self._ensure_whitelist_entry(folder, user.minecraft_uuid):
                added += 1
        if added:
            log.info(
                "whitelisted %d existing members on new server %s",
                added,
                server.alias,
            )

    async def _sync_user_to_whitelists(self, user: MinecraftUser) -> None:
        """Whitelist a newly-registered user on every managed server.

        Running servers get the change applied over RCON immediately; stopped
        servers get their ``whitelist.json`` updated so the entry is present
        on the next start.
        """
        servers = await self._repository.list_servers()
        if not servers:
            return
        applied = 0
        for server in servers:
            folder = Path(server.folder_path)
            if server.id in self._processes:
                try:
                    await self._rcon_exec(server, f"whitelist add {user.minecraft_uuid}")
                    applied += 1
                    continue
                except MinecraftRconError:
                    log.warning(
                        "RCON whitelist add failed for %s (uuid=%s); falling back to file",
                        server.alias,
                        user.minecraft_uuid,
                    )
            if self._ensure_whitelist_entry(folder, user.minecraft_uuid):
                applied += 1
        log.info(
            "auto-whitelist applied: discord_id=%s servers=%s applied=%s",
            user.discord_id,
            len(servers),
            applied,
        )

    # ------------------------------------------------------------------
    # Runtime files
    # ------------------------------------------------------------------

    async def _ensure_runtime_files(self, server: MinecraftServer, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self._ensure_static_files(folder, server.alias, server.port)
        self._ensure_paper_global_config(folder)
        jar = folder / self._jar_name()
        if not jar.exists():
            await self._download_server_jar(jar, self._settings.mc_server_version)
        if self._is_paper():
            legacy = folder / "server.jar"
            if legacy.exists():
                legacy.unlink()

    def _is_paper(self) -> bool:
        flavor = (self._settings.mc_server_flavor or "paper").strip().lower()
        if flavor not in ("paper", "vanilla"):
            raise MinecraftProcessError(
                f"Unknown MC_SERVER_FLAVOR '{flavor}' (expected 'paper' or 'vanilla')"
            )
        return flavor == "paper"

    def _jar_name(self) -> str:
        return "paper.jar" if self._is_paper() else "server.jar"

    async def _download_server_jar(self, jar: Path, version: str) -> None:
        if self._is_paper():
            await self._download_paper_jar(jar, version)
        else:
            await self._download_vanilla_jar(jar, version)

    async def _download_paper_jar(self, jar: Path, version: str) -> None:
        base = "https://fill.papermc.io/v3/projects/paper"
        headers = {"User-Agent": self._user_agent}
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(f"{base}/versions/{version}/builds") as resp:
                    if resp.status == 404:
                        raise MinecraftProcessError(f"Paper version '{version}' not found")
                    resp.raise_for_status()
                    builds = await resp.json()
                stable = [b for b in builds if b.get("channel") == "STABLE"]
                if not stable:
                    raise MinecraftProcessError(
                        f"No stable Paper build available for version '{version}'"
                    )
                build = max(stable, key=lambda b: b.get("id") or 0)
                url = (build.get("downloads") or {}).get("server:default", {}).get("url")
                if not url:
                    raise MinecraftProcessError(
                        f"No application download for Paper version '{version}'"
                    )
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    tmp = jar.with_suffix(".jar.download")
                    with open(tmp, "wb") as handle:
                        async for chunk in resp.content.iter_chunked(1 << 16):
                            handle.write(chunk)
                tmp.replace(jar)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MinecraftProcessError(f"Failed to download Paper server jar: {exc}") from exc
        log.info(
            "Paper server jar downloaded: version=%s build=%s path=%s",
            version,
            build.get("id"),
            str(jar),
        )

    async def _download_vanilla_jar(self, jar: Path, version: str) -> None:
        manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
        jar_url: str | None = None
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(manifest_url) as resp:
                    resp.raise_for_status()
                    manifest = await resp.json()
                versions = {v["id"]: v for v in manifest.get("versions", [])}
                entry = versions.get(version)
                if entry is None or "url" not in entry:
                    raise MinecraftProcessError(f"Minecraft version '{version}' not found")
                async with session.get(entry["url"]) as resp:
                    resp.raise_for_status()
                    version_meta = await resp.json()
                downloads = version_meta.get("downloads", {})
                jar_url = downloads.get("server", {}).get("url")
                if jar_url is None:
                    raise MinecraftProcessError(f"No server download for version '{version}'")
                async with session.get(jar_url) as resp:
                    resp.raise_for_status()
                    tmp = jar.with_suffix(".jar.download")
                    with open(tmp, "wb") as handle:
                        async for chunk in resp.content.iter_chunked(1 << 16):
                            handle.write(chunk)
                tmp.replace(jar)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MinecraftProcessError(f"Failed to download server jar: {exc}") from exc
        log.info("Minecraft server jar downloaded: version=%s path=%s", version, str(jar))

    # ------------------------------------------------------------------
    # Process / monitor
    # ------------------------------------------------------------------

    async def _monitor_loop(self, server: MinecraftServer) -> None:
        interval = max(1, self._settings.mc_monitor_interval_seconds)
        while server.id in self._processes:
            await asyncio.sleep(interval)
            if server.id not in self._processes:
                break
            proc = self._processes.get(server.id)
            if proc is not None and proc.returncode is not None:
                log.warning("Minecraft server process exited unexpectedly: server_id=%s", server.id)
                await self._cleanup_runtime(server.id)
                break
            try:
                _, count = await self._list_players(server)
            except MinecraftRconError:
                continue
            except Exception:  # pragma: no cover - defensive
                continue
            await self._repository.upsert_session(server.id, count)
            self._handle_auto_shutdown(server.id, count)

    def _handle_auto_shutdown(self, server_id: int, count: int) -> None:
        if count > 0:
            self._cancel_shutdown_timer(server_id)
        else:
            self._ensure_shutdown_timer(server_id)

    def _ensure_shutdown_timer(self, server_id: int) -> None:
        if server_id in self._shutdown_timers:
            return
        delay = max(1, self._settings.mc_idle_shutdown_seconds)
        log.info("Auto shutdown timer started: server_id=%s seconds=%s", server_id, delay)

        async def _timer() -> None:
            await asyncio.sleep(delay)
            if server_id in self._processes:
                log.info("Auto shutdown executed: server_id=%s", server_id)
                server = await self._repository.find_server(server_id)
                if server is not None:
                    await self._stop_server(server)

        self._shutdown_timers[server_id] = asyncio.create_task(
            _timer(), name=f"mc-idle-{server_id}"
        )

    def _cancel_shutdown_timer(self, server_id: int) -> None:
        timer = self._shutdown_timers.pop(server_id, None)
        if timer is None:
            return
        # The idle timer stops the server itself; cancelling the current
        # task from within would abort the in-flight stop.
        if asyncio.current_task() is not timer:
            timer.cancel()
        log.info("Auto shutdown timer removed: server_id=%s", server_id)

    async def _stop_server(self, server: MinecraftServer) -> None:
        proc = self._processes.get(server.id)
        if proc is None:
            raise MinecraftProcessError(f"Server '{server.alias}' is not running")

        rcon_ok = True
        try:
            await self._rcon_exec(server, "stop")
        except MinecraftRconError as exc:
            rcon_ok = False
            log.warning("RCON stop failed for %s: %s", server.alias, exc)

        self._cancel_shutdown_timer(server.id)
        monitor = self._monitors.pop(server.id, None)
        if monitor is not None:
            monitor.cancel()

        if rcon_ok:
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._stop_grace)
            except TimeoutError:
                pass

        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._stop_kill_grace)
            except TimeoutError:
                proc.kill()
                await proc.wait()

        await self._cleanup_runtime(server.id)
        log.info("Minecraft server stopped: alias=%s", server.alias)

    async def _cleanup_runtime(self, server_id: int) -> None:
        """Drop monitor/timer/process state and persist the stopped status."""
        self._cancel_shutdown_timer(server_id)
        monitor = self._monitors.pop(server_id, None)
        if monitor is not None:
            monitor.cancel()
        self._processes.pop(server_id, None)
        await self._repository.set_status(server_id, STATUS_STOPPED)

    # ------------------------------------------------------------------
    # Player / RCON / permission helpers
    # ------------------------------------------------------------------

    async def _list_players(self, server: MinecraftServer) -> tuple[list[str], int]:
        output = await self._rcon_exec(server, "list")
        return self._parse_list(output)

    @staticmethod
    def _parse_list(output: str) -> tuple[list[str], int]:
        """Parse the ``list`` command output.

        Handles both the legacy format (Minecraft < 1.20.3)::

            There are 2/20 players online: a, b

        and the newer format (Minecraft >= 1.20.3, which changed the wording)::

            There are 2 of a max of 20 players online: a, b
        """
        line = output.strip()
        # Remove common RCON prefixes like "[HH:MM:SS INFO]: "
        if line.startswith("[") and "]: " in line:
            line = line.split("]: ", 1)[-1]

        # Extract player count (both legacy "X/Y" and new "X of a max of Y")
        match = re.search(r"(\d+)(?:/\d+| of a max of \d+) players online", line)
        count = int(match.group(1)) if match else 0

        # Extract player names (everything after "online: ")
        names: list[str] = []
        if " online: " in line:
            raw = line.split(" online: ", 1)[-1].strip()
            if raw:
                names = [n.strip() for n in raw.split(",") if n.strip()]
        return names, count

    async def _rcon_exec(self, server: MinecraftServer, command: str) -> str:
        host = self._settings.mc_rcon_host
        port, password = self._rcon_connection_details(server)

        # Only connection / auth failures are retried. Once the command has
        # been handed to the server it must never be re-sent: RCON commands are
        # not idempotent, so retrying after a response timeout would execute the
        # command multiple times on the server (e.g. summoning the same entity
        # 3 times) even though each attempt "succeeded" server-side.
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            client = RCONClient(host, port)
            try:
                await client.connect(password, timeout=30.0)
                result = await client.command(command, timeout=30.0)
                await client.close()
                return result
            except (RCONConnectionError, RCONAuthError) as exc:
                # Connection could not be established / auth rejected — the
                # command was never sent, so retrying is safe.
                last_error = exc
                await client.close()
                if attempt < max_retries - 1:
                    # Wait before retrying (exponential backoff)
                    await asyncio.sleep(2**attempt)
            except RCONError as exc:
                # The command was already sent and the server may have executed
                # it. Do NOT retry — that would execute it again.
                last_error = exc
                await client.close()
                break

        raise MinecraftRconError(f"RCON failed for '{server.alias}': {last_error}") from last_error

    def _rcon_port_for_port(self, port: int) -> int:
        return self._settings.mc_rcon_port + (port - DEFAULT_PORT)

    def _rcon_connection_details(self, server: MinecraftServer) -> tuple[int, str]:
        folder = Path(server.folder_path)
        props = self._load_server_properties(folder)
        port = self._parse_int(props.get("rcon.port"))
        if port is None:
            port = self._rcon_port_for_port(server.port)

        password = self._read_text(folder / ".rcon_password").strip()
        if not password:
            password = props.get("rcon.password", "").strip()
        if not password:
            password = self._rcon_password_for_alias(server.alias)
        return port, password

    def _rcon_password_for_alias(self, alias: str) -> str:
        import hashlib

        digest = hashlib.sha256(
            f"{self._settings.mc_rcon_password_secret}:{alias}".encode()
        ).hexdigest()
        return digest[:16]

    @staticmethod
    def _load_server_properties(folder: Path) -> dict[str, str]:
        path = folder / "server.properties"
        data: dict[str, str] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
        except OSError:
            return {}
        return data

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @classmethod
    def _read_server_port(cls, folder: Path) -> int | None:
        """Read the ``server-port`` from a folder's ``server.properties``."""
        props = cls._load_server_properties(folder)
        return cls._parse_int(props.get("server-port"))

    async def _is_op(self, server: MinecraftServer, discord_id: int) -> bool:
        user = await self._repository.find_user_by_discord(discord_id)
        if user is None:
            return False
        ops = self._load_ops(Path(server.folder_path))
        return self._normalize_uuid(user.minecraft_uuid) in ops

    @staticmethod
    def _normalize_uuid(value: str) -> str | None:
        try:
            parsed = uuid_lib.UUID(value)
        except (ValueError, AttributeError):
            return None
        return parsed.hex

    @staticmethod
    def _canonical_uuid(value: str) -> str | None:
        try:
            parsed = uuid_lib.UUID(value)
        except (ValueError, AttributeError):
            return None
        return str(parsed)

    def _load_ops(self, folder: Path) -> set[str]:
        ops_file = folder / "ops.json"
        if not ops_file.exists():
            return set()
        try:
            data = json.loads(ops_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        ops: set[str] = set()
        for entry in data if isinstance(data, list) else []:
            uid = entry.get("uuid") if isinstance(entry, dict) else None
            normalized = self._normalize_uuid(uid) if uid else None
            if normalized:
                ops.add(normalized)
        return ops

    @staticmethod
    def _add_to_ops(folder: Path, minecraft_uuid: str) -> None:
        _append_json_entry(
            folder / "ops.json",
            {"uuid": minecraft_uuid, "name": "", "level": 4, "bypassesPlayerLimit": True},
        )

    @staticmethod
    def _add_to_whitelist(folder: Path, minecraft_uuid: str) -> None:
        _append_json_entry(
            folder / "whitelist.json",
            {"uuid": minecraft_uuid, "name": ""},
        )

    @classmethod
    def _ensure_whitelist_entry(cls, folder: Path, minecraft_uuid: str) -> bool:
        """Add a UUID to ``whitelist.json`` if missing.

        Returns:
            True if a new entry was written, False when it was already present.
        """
        whitelist = _load_json_list(folder / "whitelist.json")
        normalized = cls._normalize_uuid(minecraft_uuid)
        if normalized is None:
            return False
        existing = {
            cls._normalize_uuid(e.get("uuid"))
            for e in whitelist
            if isinstance(e, dict) and cls._normalize_uuid(e.get("uuid"))
        }
        if normalized in existing:
            return False
        whitelist.append({"uuid": minecraft_uuid, "name": ""})
        _write_json_list(folder / "whitelist.json", whitelist)
        return True

    @classmethod
    def _remove_whitelist_entry(cls, folder: Path, minecraft_uuid: str) -> bool:
        """Remove a UUID from ``whitelist.json`` if present.

        Returns:
            True if an entry was removed, False when it was not present.
        """
        whitelist = _load_json_list(folder / "whitelist.json")
        normalized = cls._normalize_uuid(minecraft_uuid)
        if normalized is None:
            return False
        kept = [
            e
            for e in whitelist
            if not (isinstance(e, dict) and cls._normalize_uuid(e.get("uuid")) == normalized)
        ]
        if len(kept) == len(whitelist):
            return False
        _write_json_list(folder / "whitelist.json", kept)
        return True

    def _patch_external_properties(self, folder: Path, alias: str) -> None:
        """Enable RCON and whitelist enforcement on an imported folder.

        Only updates the RCON / whitelist keys, preserving the rest of the
        folder's existing ``server.properties`` (``server-port``, ``motd``,
        ``online-mode``, ...). If the file is missing, the standard bootstrap
        (``_ensure_static_files``) writes a full default one on first start.
        """
        path = folder / "server.properties"
        if not path.exists():
            return

        rcon_port = self._rcon_port_for_port(self._read_server_port(folder) or DEFAULT_PORT)
        password = self._rcon_password_for_alias(alias)
        values = {
            "enable-rcon": "true",
            "rcon.port": str(rcon_port),
            "rcon.password": password,
            "white-list": "true",
            "enforce-whitelist": "true",
        }

        lines: list[str] = []
        seen: set[str] = set()
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lines.append(raw)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                if key not in seen:
                    lines.append(f"{key}={values[key]}")
                    seen.add(key)
                continue
            lines.append(raw)
        for key, value in values.items():
            if key not in seen:
                lines.append(f"{key}={value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (folder / ".rcon_password").write_text(password, encoding="utf-8")

    async def _sync_whitelist_and_ops(self, server: MinecraftServer) -> None:
        """Apply every registered Discord↔UUID member to the server's lists.

        Merges into the folder's existing ``whitelist.json`` / ``ops.json``,
        deduplicating by UUID so members are never duplicated when a server
        is migrated more than once.
        """
        users = await self._repository.list_users()
        if not users:
            log.info("no registered members to apply to migrated server %s", server.alias)
            return

        folder = Path(server.folder_path)
        whitelist = _load_json_list(folder / "whitelist.json")
        ops = _load_json_list(folder / "ops.json")
        whitelist_uuids = {
            self._normalize_uuid(e.get("uuid"))
            for e in whitelist
            if isinstance(e, dict) and self._normalize_uuid(e.get("uuid"))
        }
        op_uuids = {
            self._normalize_uuid(e.get("uuid"))
            for e in ops
            if isinstance(e, dict) and self._normalize_uuid(e.get("uuid"))
        }

        added_whitelist = 0
        added_ops = 0
        for user in users:
            normalized = self._normalize_uuid(user.minecraft_uuid)
            if not normalized:
                continue
            if normalized not in whitelist_uuids:
                whitelist.append({"uuid": user.minecraft_uuid, "name": ""})
                whitelist_uuids.add(normalized)
                added_whitelist += 1
            if normalized not in op_uuids:
                ops.append(
                    {
                        "uuid": user.minecraft_uuid,
                        "name": "",
                        "level": 4,
                        "bypassesPlayerLimit": True,
                    }
                )
                op_uuids.add(normalized)
                added_ops += 1

        if added_whitelist:
            _write_json_list(folder / "whitelist.json", whitelist)
        if added_ops:
            _write_json_list(folder / "ops.json", ops)
        log.info(
            "whitelist/ops synced for migrated server: alias=%s added_whitelist=%s added_ops=%s",
            server.alias,
            added_whitelist,
            added_ops,
        )

    # ------------------------------------------------------------------
    # Ports / cleanup
    # ------------------------------------------------------------------

    async def _find_free_port(self) -> int:
        port = await self._first_available_port()
        if port is None:
            start, end = self._port_range()
            raise MinecraftPortConflict(f"No free port found in range {start}-{end}")
        return port

    async def _first_available_port(self) -> int | None:
        """Return the lowest free port in the published range, or ``None``."""
        start, end = self._port_range()
        for port in range(start, end + 1):
            conflict = await self._repository.find_server_by_port(port)
            if conflict is not None:
                continue
            if self._is_port_free(port):
                return port
        return None

    @staticmethod
    def _is_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                return False
        return True

    @staticmethod
    def _remove_folder(folder: Path) -> None:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)


def _append_json_entry(path: Path, entry: dict[str, Any]) -> None:
    """Append an entry to a JSON-list file, preserving existing content."""
    data: list[Any] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = []
    if not isinstance(data, list):
        data = []
    data.append(entry)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_json_list(path: Path) -> list[Any]:
    """Load a JSON-list file, tolerating missing/corrupt content."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_json_list(path: Path, entries: list[Any]) -> None:
    """Write a JSON-list file with indentation."""
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
