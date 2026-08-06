"""Minecraft world backup service.

Creates per-server world backups as ``<server>-YYYYMMDD-HHmmss.backup.zip``
under a configurable root directory, following an atomic save-and-copy flow so
a failed backup never destroys existing backups. A retention policy removes
backups older than the configured window only when a newer backup exists for
the same server (each server keeps at least its newest backup).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import Settings
from core.exceptions import (
    MinecraftBackupError,
    MinecraftBackupInProgress,
    MinecraftServerNotFound,
)
from core.logger import get_logger
from features.minecraft.models import MinecraftServer
from services.minecraft_service import MinecraftService

log = get_logger(__name__)

# Default world folder name used when server.properties has no ``level-name``.
DEFAULT_LEVEL_NAME = "world"

# Matches the trailing timestamp of a backup filename, e.g. ``survival-20260806-212700.backup.zip``.
_BACKUP_FILE_RE = re.compile(r"-(\d{8})-(\d{6})\.backup\.zip$")

# Static server configuration files bundled into every archive.
_CONFIG_FILES = ("server.properties", "bukkit.yml", "spigot.yml")

# paper-global.yml lives under config/ on Paper.
_PAPER_GLOBAL_PATH = "config/paper-global.yml"


class MinecraftBackupService:
    """Handles Minecraft per-server world backups and retention cleanup."""

    def __init__(self, minecraft_service: MinecraftService, settings: Settings) -> None:
        self._mc = minecraft_service
        self._settings = settings
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_backup(
        self, server_name: str, *, created_by: int | None = None
    ) -> Path:
        """Create a world backup for the named server.

        Args:
            server_name: The server alias (defines the backup folder).
            created_by: Optional Discord id of the requesting user (logging).

        Returns:
            The path of the newly created archive.

        Raises:
            MinecraftServerNotFound: Unknown server.
            MinecraftBackupInProgress: A backup for this server is already running.
            MinecraftBackupError: The backup failed; existing backups are kept.
        """
        server = await self._mc.find_server(server_name)
        log.info("backup started server=%s", server_name)

        lock = await self._get_lock(server_name)
        if lock.locked():
            raise MinecraftBackupInProgress(
                f"Backup already in progress for '{server_name}'"
            )
        async with lock:
            try:
                return await self._create_backup_locked(server)
            except (MinecraftBackupError, MinecraftServerNotFound):
                raise
            except Exception as exc:  # defensive — wrap anything unexpected
                raise MinecraftBackupError(
                    f"Backup failed for '{server_name}': {exc}"
                ) from exc

    async def cleanup_old_backups(self, server_name: str) -> list[Path]:
        """Delete expired backups for a server, always keeping the newest one.

        A backup is removed only when it is older than the retention window
        AND a newer backup exists for the same server.

        Args:
            server_name: The server alias.

        Returns:
            The list of removed backup paths.
        """
        backup_dir = self._backup_root(server_name)
        if not backup_dir.is_dir():
            return []

        backups = sorted(self._list_backups(backup_dir))
        # Keep at least the newest backup; older ones are candidates.
        if len(backups) <= 1:
            return []

        removed: list[Path] = []
        for path in backups[:-1]:
            if not self._is_expired(path):
                continue
            try:
                path.unlink()
            except OSError as exc:
                log.warning("failed to remove old backup file=%s: %s", path.name, exc)
                continue
            removed.append(path)
            log.info("removed old backup file=%s", path.name)
        return removed

    async def cleanup_all(self) -> dict[str, list[Path]]:
        """Run retention cleanup for every registered server.

        Returns:
            Mapping of server name → removed backup paths.
        """
        servers = await self._mc.list_servers()
        result: dict[str, list[Path]] = {}
        for server in servers:
            result[server.alias] = await self.cleanup_old_backups(server.alias)
        return result

    # ------------------------------------------------------------------
    # Backup internals
    # ------------------------------------------------------------------

    async def _create_backup_locked(self, server: MinecraftServer) -> Path:
        folder = Path(server.folder_path)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{server.alias}-{timestamp}.backup.zip"
        backup_root = self._backup_root(server.alias)
        staging = self._staging_root() / f"{server.alias}-{timestamp}"
        staging.mkdir(parents=True, exist_ok=True)

        running = await self._mc.is_running(server.alias)
        try:
            if running:
                await self._mc.send_rcon(server.alias, "save-off")
                log.info("saving world server=%s", server.alias)
                await self._mc.send_rcon(server.alias, "save-all flush")

            await asyncio.to_thread(self._copy_server_data, folder, staging)
            log.info("creating archive file=%s", filename)
            await asyncio.to_thread(self._create_archive, staging, filename)
            await asyncio.to_thread(self._verify_archive, staging / filename)

            backup_root.mkdir(parents=True, exist_ok=True)
            final_path = backup_root / filename
            await asyncio.to_thread(self._move_archive, staging / filename, final_path)
            log.info("backup completed file=%s", filename)
        except (MinecraftBackupError, OSError) as exc:
            log.error("backup failed server=%s: %s", server.alias, exc, exc_info=exc)
            raise MinecraftBackupError(
                f"Backup failed for '{server.alias}': {exc}"
            ) from exc
        finally:
            # Re-enable world saving and clean up the staging directory even on failure.
            if running:
                try:
                    await self._mc.send_rcon(server.alias, "save-on")
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning(
                        "failed to re-enable world saving server=%s: %s",
                        server.alias,
                        exc,
                    )
            await asyncio.to_thread(self._cleanup_staging, staging)

        # Retention cleanup runs after a successful backup so expired archives
        # are pruned automatically.
        removed = await self.cleanup_old_backups(server.alias)
        if removed:
            log.info(
                "removed old backup server=%s count=%s", server.alias, len(removed)
            )
        return final_path

    @staticmethod
    def _copy_server_data(folder: Path, staging: Path) -> None:
        """Copy world / config / plugin data into the staging directory."""
        props = MinecraftService._load_server_properties(folder)
        level_name = props.get("level-name", DEFAULT_LEVEL_NAME).strip() or DEFAULT_LEVEL_NAME

        # World folders (level-name + nether + end dimensions).
        for name in (level_name, f"{level_name}_nether", f"{level_name}_the_end"):
            src = folder / name
            if src.is_dir():
                shutil.copytree(src, staging / name, symlinks=True)

        # Static configuration files.
        for name in _CONFIG_FILES:
            src = folder / name
            if src.is_file():
                shutil.copy2(src, staging / name)

        # Paper global config (config/paper-global.yml).
        paper_global = folder / _PAPER_GLOBAL_PATH
        if paper_global.is_file():
            dest = staging / "config"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paper_global, dest / "paper-global.yml")

        # Plugin data.
        plugins = folder / "plugins"
        if plugins.is_dir():
            shutil.copytree(plugins, staging / "plugins", symlinks=True)

    @staticmethod
    def _create_archive(staging: Path, filename: str) -> None:
        """Build the backup zip from the staging directory contents.

        Directory entries are written explicitly so empty folders (e.g. an
        unused ``world_nether/``) are preserved inside the archive.
        """
        zip_path = staging / filename
        entries = sorted(
            staging.rglob("*"),
            key=lambda p: p.relative_to(staging).as_posix(),
        )
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in entries:
                if path == zip_path:
                    continue
                arcname = path.relative_to(staging).as_posix()
                if path.is_dir():
                    info = zipfile.ZipInfo(arcname + "/")
                    archive.writestr(info, b"")
                elif path.is_file():
                    archive.write(path, arcname)
                # Broken symlinks / special files are skipped.

    @staticmethod
    def _verify_archive(zip_path: Path) -> None:
        """Raise if the archive is corrupt or empty."""
        with zipfile.ZipFile(zip_path, "r") as archive:
            bad = archive.testzip()
            if bad is not None:
                raise MinecraftBackupError(
                    f"Archive is corrupt ({bad}): {zip_path.name}"
                )
            if not archive.namelist():
                raise MinecraftBackupError(f"Archive is empty: {zip_path.name}")

    @staticmethod
    def _move_archive(src: Path, dest: Path) -> None:
        src.replace(dest)

    @staticmethod
    def _cleanup_staging(staging: Path) -> None:
        """Remove the staging directory and its (now empty) temp root."""
        shutil.rmtree(staging, ignore_errors=True)
        temp_root = staging.parent
        try:
            temp_root.rmdir()  # only succeeds when empty
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Retention internals
    # ------------------------------------------------------------------

    def _is_expired(self, path: Path) -> bool:
        timestamp = self._parse_backup_timestamp(path.name)
        if timestamp is None:
            return False  # unknown naming convention — never delete
        age = datetime.now() - timestamp
        return age > timedelta(days=max(0, self._settings.mc_backup_retention_days))

    @staticmethod
    def _parse_backup_timestamp(name: str) -> datetime | None:
        match = _BACKUP_FILE_RE.search(name)
        if not match:
            return None
        date_part, time_part = match.groups()
        try:
            return datetime.strptime(f"{date_part}-{time_part}", "%Y%m%d-%H%M%S")
        except ValueError:
            return None

    @staticmethod
    def _list_backups(backup_dir: Path) -> list[Path]:
        return [
            path
            for path in backup_dir.iterdir()
            if path.is_file() and path.name.endswith(".backup.zip")
        ]

    # ------------------------------------------------------------------
    # Paths / locking
    # ------------------------------------------------------------------

    def _backup_root(self, server_name: str) -> Path:
        return self._root() / server_name

    def _staging_root(self) -> Path:
        return self._root() / ".tmp"

    def _root(self) -> Path:
        return Path(self._settings.mc_backup_directory).expanduser()

    async def _get_lock(self, server_name: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(server_name)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[server_name] = lock
            return lock
