"""Tests for the Minecraft world backup service.

Covers per-server backup creation (path / filename / zip contents / required
files), the save flow when the server is running, failure safety (existing
backups kept, staging cleaned up, world saving resumed), and the retention
policy (the four documented cases).
"""

from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.settings import Settings
from core.exceptions import (
    MinecraftBackupError,
    MinecraftBackupInProgress,
    MinecraftServerNotFound,
)
from features.minecraft.models import MinecraftServer
from services.minecraft_backup_service import MinecraftBackupService


class _FakeMC:
    """In-memory stand-in for the parts of MinecraftService used by backups."""

    def __init__(self, server: MinecraftServer, running: bool = False) -> None:
        self._server = server
        self._running = running
        self.commands: list[str] = []

    async def find_server(self, name: str) -> MinecraftServer:
        if self._server.alias != name:
            raise MinecraftServerNotFound(name)
        return self._server

    async def is_running(self, name: str) -> bool:
        return self._running

    async def send_rcon(self, name: str, command: str) -> str:
        self.commands.append(command)
        return "ok"

    async def list_servers(self) -> list[MinecraftServer]:
        return [self._server]


def _make_settings(root: Path, retention_days: int = 90) -> Settings:
    return Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
        mc_backup_directory=str(root / "backups"),
        mc_backup_retention_days=retention_days,
    )


def _make_server(folder: Path, alias: str = "survival") -> MinecraftServer:
    now = datetime.now(UTC)
    return MinecraftServer(
        id=1,
        alias=alias,
        folder_path=str(folder),
        port=25565,
        status="stopped",
        created_by=123,
        created_at=now,
        updated_at=now,
    )


def _make_world_folder(folder: Path) -> None:
    (folder / "world" / "region").mkdir(parents=True)
    (folder / "world" / "region" / "r.0.0.mca").write_bytes(b"\x00\x01")
    (folder / "world_nether").mkdir()
    (folder / "world_the_end").mkdir()
    (folder / "server.properties").write_text("level-name=world\n", encoding="utf-8")
    (folder / "bukkit.yml").write_text("settings: {}\n", encoding="utf-8")
    (folder / "spigot.yml").write_text("settings: {}\n", encoding="utf-8")
    (folder / "config").mkdir()
    (folder / "config" / "paper-global.yml").write_text(
        "spark:\n  enabled: false\n", encoding="utf-8"
    )
    (folder / "plugins" / "Example").mkdir(parents=True)
    (folder / "plugins" / "Example" / "config.yml").write_text(
        "enabled: true\n", encoding="utf-8"
    )


def _make_backup(backup_dir: Path, server: str, age_days: int) -> Path:
    ts = datetime.now() - timedelta(days=age_days)
    name = f"{server}-{ts.strftime('%Y%m%d-%H%M%S')}.backup.zip"
    path = backup_dir / name
    path.write_bytes(b"dummy")
    return path


# ----------------------------------------------------------------------
# Backup tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_creates_archive_with_required_files(tmp_path: Path) -> None:
    folder = tmp_path / "server"
    _make_world_folder(folder)
    server = _make_server(folder)
    service = MinecraftBackupService(_FakeMC(server), _make_settings(tmp_path))

    backup_path = await service.create_backup("survival")

    # Per-server backup directory + correct filename convention.
    backup_dir = tmp_path / "backups" / "survival"
    assert backup_dir.is_dir()
    assert backup_path.parent == backup_dir
    assert re.match(r"^survival-\d{8}-\d{6}\.backup\.zip$", backup_path.name)

    # Valid zip containing the required world / config / plugin data.
    with zipfile.ZipFile(backup_path, "r") as archive:
        names = archive.namelist()
        assert "world/region/r.0.0.mca" in names
        assert "world_nether/" in names or any(n.startswith("world_nether/") for n in names)
        assert "world_the_end/" in names or any(n.startswith("world_the_end/") for n in names)
        assert "server.properties" in names
        assert "bukkit.yml" in names
        assert "spigot.yml" in names
        assert "config/paper-global.yml" in names
        assert "plugins/Example/config.yml" in names


@pytest.mark.asyncio
async def test_backup_runs_save_flow_when_running(tmp_path: Path) -> None:
    folder = tmp_path / "server"
    _make_world_folder(folder)
    server = _make_server(folder)
    fake = _FakeMC(server, running=True)
    service = MinecraftBackupService(fake, _make_settings(tmp_path))

    await service.create_backup("survival")

    assert fake.commands == ["save-off", "save-all flush", "save-on"]


@pytest.mark.asyncio
async def test_backup_skips_rcon_when_stopped(tmp_path: Path) -> None:
    folder = tmp_path / "server"
    _make_world_folder(folder)
    server = _make_server(folder)
    fake = _FakeMC(server, running=False)
    service = MinecraftBackupService(fake, _make_settings(tmp_path))

    await service.create_backup("survival")

    assert fake.commands == []


@pytest.mark.asyncio
async def test_backup_failure_keeps_existing_and_cleans_up(tmp_path: Path) -> None:
    folder = tmp_path / "server"
    _make_world_folder(folder)
    server = _make_server(folder)
    fake = _FakeMC(server, running=True)
    service = MinecraftBackupService(fake, _make_settings(tmp_path))

    def boom(staging: Path, filename: str) -> None:
        raise OSError("zip failed")

    service._create_archive = boom  # type: ignore[method-assign]

    with pytest.raises(MinecraftBackupError):
        await service.create_backup("survival")

    # World saving was re-enabled and the staging directory was cleaned up.
    assert "save-on" in fake.commands
    assert not (tmp_path / "backups" / ".tmp").exists()
    backup_dir = tmp_path / "backups" / "survival"
    assert not backup_dir.exists() or not any(backup_dir.iterdir())


@pytest.mark.asyncio
async def test_backup_in_progress_raises(tmp_path: Path) -> None:
    folder = tmp_path / "server"
    _make_world_folder(folder)
    server = _make_server(folder)
    service = MinecraftBackupService(_FakeMC(server), _make_settings(tmp_path))

    lock = await service._get_lock("survival")
    await lock.acquire()
    try:
        with pytest.raises(MinecraftBackupInProgress):
            await service.create_backup("survival")
    finally:
        lock.release()


def test_parse_backup_timestamp_with_hyphenated_server() -> None:
    ts = MinecraftBackupService._parse_backup_timestamp(
        "survival-2-20260101-000000.backup.zip"
    )
    assert ts == datetime(2026, 1, 1, 0, 0, 0)


# ----------------------------------------------------------------------
# Retention tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_case1_old_with_newer_is_deleted(tmp_path: Path) -> None:
    service = MinecraftBackupService(
        _FakeMC(_make_server(tmp_path)), _make_settings(tmp_path)
    )
    backup_dir = tmp_path / "backups" / "survival"
    backup_dir.mkdir(parents=True)
    old = _make_backup(backup_dir, "survival", age_days=120)
    new = _make_backup(backup_dir, "survival", age_days=0)

    removed = await service.cleanup_old_backups("survival")

    assert removed == [old]
    assert not old.exists()
    assert new.exists()


@pytest.mark.asyncio
async def test_retention_case2_only_old_is_kept(tmp_path: Path) -> None:
    service = MinecraftBackupService(
        _FakeMC(_make_server(tmp_path)), _make_settings(tmp_path)
    )
    backup_dir = tmp_path / "backups" / "survival"
    backup_dir.mkdir(parents=True)
    old = _make_backup(backup_dir, "survival", age_days=120)

    removed = await service.cleanup_old_backups("survival")

    assert removed == []
    assert old.exists()


@pytest.mark.asyncio
async def test_retention_case3_recent_is_kept(tmp_path: Path) -> None:
    service = MinecraftBackupService(
        _FakeMC(_make_server(tmp_path)), _make_settings(tmp_path)
    )
    backup_dir = tmp_path / "backups" / "survival"
    backup_dir.mkdir(parents=True)
    recent = _make_backup(backup_dir, "survival", age_days=10)
    newer = _make_backup(backup_dir, "survival", age_days=0)

    removed = await service.cleanup_old_backups("survival")

    assert removed == []
    assert recent.exists()
    assert newer.exists()


@pytest.mark.asyncio
async def test_retention_case4_servers_do_not_interfere(tmp_path: Path) -> None:
    service = MinecraftBackupService(
        _FakeMC(_make_server(tmp_path)), _make_settings(tmp_path)
    )
    surv_dir = tmp_path / "backups" / "survival"
    crea_dir = tmp_path / "backups" / "creative"
    surv_dir.mkdir(parents=True)
    crea_dir.mkdir(parents=True)
    surv_old = _make_backup(surv_dir, "survival", age_days=120)
    surv_new = _make_backup(surv_dir, "survival", age_days=0)
    crea_old = _make_backup(crea_dir, "creative", age_days=120)
    crea_new = _make_backup(crea_dir, "creative", age_days=0)

    removed = await service.cleanup_old_backups("survival")

    # Survival cleanup only touches survival backups.
    assert removed == [surv_old]
    assert surv_new.exists()
    assert crea_old.exists()
    assert crea_new.exists()
