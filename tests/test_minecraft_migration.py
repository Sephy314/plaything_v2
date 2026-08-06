"""Tests for the external server folder migration feature.

Covers ``MinecraftService.migrate_external_folder`` and
``MinecraftService.available_external_folders``: folder discovery,
PostgreSQL registration, server.properties patching, and whitelist/OP sync.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.settings import Settings
from core.exceptions import MinecraftAliasExists, MinecraftFolderNotFound
from features.minecraft.models import MinecraftServer, MinecraftUser
from services import minecraft_service

_UUID_A = "12345678-1234-1234-1234-123456789012"
_UUID_B = "abcdefab-abcd-abcd-abcd-abcdefabcdef"


def _make_user(discord_id: int, minecraft_uuid: str) -> MinecraftUser:
    return MinecraftUser(
        discord_id=discord_id,
        minecraft_uuid=minecraft_uuid,
        created_at=datetime.now(UTC),
    )


def _make_settings(parent: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
        mc_parent_directory=str(parent),
    )


class _FakeRepo:
    """In-memory stand-in for MinecraftRepository."""

    def __init__(self, users: list[MinecraftUser] | None = None) -> None:
        self._servers: list[MinecraftServer] = []
        self._next_id = 1
        self._users = users or []

    async def find_server_by_alias(self, alias: str) -> MinecraftServer | None:
        return next((s for s in self._servers if s.alias == alias), None)

    async def find_server_by_folder(self, folder_path: str) -> MinecraftServer | None:
        return next((s for s in self._servers if s.folder_path == folder_path), None)

    async def find_server_by_port(self, port: int) -> MinecraftServer | None:
        return next((s for s in self._servers if s.port == port), None)

    async def list_servers(self) -> list[MinecraftServer]:
        return list(self._servers)

    async def register_server(
        self, alias: str, folder_path: str, port: int, created_by: int, conn=None
    ) -> MinecraftServer:
        server = MinecraftServer(
            id=self._next_id,
            alias=alias,
            folder_path=folder_path,
            port=port,
            status="stopped",
            created_by=created_by,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._next_id += 1
        self._servers.append(server)
        return server

    async def list_users(self) -> list[MinecraftUser]:
        return list(self._users)


@pytest.mark.asyncio
async def test_migrate_external_folder_registers_and_syncs(tmp_path: Path) -> None:
    folder = tmp_path / "external_map"
    folder.mkdir()
    (folder / "server.properties").write_text(
        "server-port=25566\nonline-mode=true\nmotd=external\n",
        encoding="utf-8",
    )
    users = [_make_user(111, _UUID_A), _make_user(222, _UUID_B)]
    repo = _FakeRepo(users)
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    server = await service.migrate_external_folder("external_map", None, 123)

    assert server.alias == "external_map"
    assert server.port == 25566  # read from the folder's server.properties
    assert server.folder_path == str(folder)

    # server.properties is patched in place: port/motd preserved, RCON+whitelist on.
    props = (folder / "server.properties").read_text(encoding="utf-8")
    assert "server-port=25566" in props
    assert "online-mode=true" in props
    assert "motd=external" in props
    assert "enable-rcon=true" in props
    assert "rcon.port=25576" in props
    assert "white-list=true" in props
    assert "enforce-whitelist=true" in props

    # Every registered member is applied to whitelist.json and ops.json.
    whitelist = json.loads((folder / "whitelist.json").read_text(encoding="utf-8"))
    ops = json.loads((folder / "ops.json").read_text(encoding="utf-8"))
    assert {e["uuid"] for e in whitelist} == {_UUID_A, _UUID_B}
    assert {e["uuid"] for e in ops} == {_UUID_A, _UUID_B}
    assert all(e["level"] == 4 for e in ops)
    assert all(e["bypassesPlayerLimit"] is True for e in ops)


@pytest.mark.asyncio
async def test_migrate_external_folder_not_found(tmp_path: Path) -> None:
    service = minecraft_service.MinecraftService(_FakeRepo(), _make_settings(tmp_path))

    with pytest.raises(MinecraftFolderNotFound):
        await service.migrate_external_folder("missing", None, 123)


@pytest.mark.asyncio
async def test_migrate_external_folder_duplicate_alias(tmp_path: Path) -> None:
    folder = tmp_path / "dupe"
    folder.mkdir()
    repo = _FakeRepo()
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    await service.migrate_external_folder("dupe", 25565, 123)

    with pytest.raises(MinecraftAliasExists):
        await service.migrate_external_folder("dupe", 25565, 123)


@pytest.mark.asyncio
async def test_migrate_external_folder_rejects_registered_folder(tmp_path: Path) -> None:
    folder = tmp_path / "reused"
    folder.mkdir()
    now = datetime.now(UTC)
    # A server registered under a different alias but owning this folder path.
    existing = MinecraftServer(
        id=9,
        alias="somewhere_else",
        folder_path=str(folder),
        port=25566,
        status="stopped",
        created_by=1,
        created_at=now,
        updated_at=now,
    )
    repo = _FakeRepo()
    repo._servers.append(existing)
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    with pytest.raises(MinecraftAliasExists):
        await service.migrate_external_folder("reused", 25565, 123)


@pytest.mark.asyncio
async def test_migrate_external_folder_merges_existing_lists(tmp_path: Path) -> None:
    folder = tmp_path / "merged"
    folder.mkdir()
    (folder / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
    (folder / "whitelist.json").write_text(
        json.dumps([{"uuid": _UUID_A, "name": "legacy"}]), encoding="utf-8"
    )
    users = [_make_user(111, _UUID_A), _make_user(222, _UUID_B)]
    repo = _FakeRepo(users)
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    await service.migrate_external_folder("merged", None, 123)

    whitelist = json.loads((folder / "whitelist.json").read_text(encoding="utf-8"))
    uuids = [e["uuid"] for e in whitelist]
    assert uuids.count(_UUID_A) == 1  # existing entry not duplicated
    assert _UUID_B in uuids

    ops = json.loads((folder / "ops.json").read_text(encoding="utf-8"))
    assert {e["uuid"] for e in ops} == {_UUID_A, _UUID_B}


@pytest.mark.asyncio
async def test_migrate_external_folder_no_users_keeps_lists(tmp_path: Path) -> None:
    folder = tmp_path / "bare"
    folder.mkdir()
    (folder / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
    service = minecraft_service.MinecraftService(_FakeRepo(), _make_settings(tmp_path))

    await service.migrate_external_folder("bare", None, 123)

    assert not (folder / "whitelist.json").exists()
    assert not (folder / "ops.json").exists()


@pytest.mark.asyncio
async def test_available_external_folders_lists_unmanaged(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    repo = _FakeRepo()
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    assert await service.available_external_folders() == ["alpha", "beta"]

    await service.migrate_external_folder("alpha", 25565, 123)

    # The registered folder is now excluded from the autocomplete list.
    assert await service.available_external_folders() == ["beta"]
