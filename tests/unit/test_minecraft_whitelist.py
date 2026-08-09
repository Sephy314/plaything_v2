"""Tests for the Minecraft whitelist / UUID-registration behavior.

Covers automatic whitelisting of DB-registered users, whitelist management by
Discord member (``whitelist_user``), and whitelisting registered members on
newly created servers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.settings import Settings
from core.exceptions import MinecraftUnauthorized
from features.minecraft.models import MinecraftServer, MinecraftUser
from services import minecraft_service

_UUID_A = "12345678-1234-1234-1234-123456789012"
_UUID_B = "abcdefab-abcd-abcd-abcd-abcdefabcdef"


def _make_server(alias: str, folder: Path, server_id: int = 1) -> MinecraftServer:
    return MinecraftServer(
        id=server_id,
        alias=alias,
        folder_path=str(folder),
        port=25565,
        status="stopped",
        created_by=999,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


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


def _read_whitelist(folder: Path) -> list[dict]:
    path = folder / "whitelist.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeRepo:
    """In-memory stand-in for MinecraftRepository."""

    def __init__(self) -> None:
        self._servers: list[MinecraftServer] = []
        self._users: dict[int, MinecraftUser] = {}

    async def list_servers(self) -> list[MinecraftServer]:
        return list(self._servers)

    async def find_server_by_alias(self, alias: str) -> MinecraftServer | None:
        return next((s for s in self._servers if s.alias == alias), None)

    async def list_users(self) -> list[MinecraftUser]:
        return list(self._users.values())

    async def upsert_user(self, discord_id: int, minecraft_uuid: str) -> MinecraftUser:
        user = _make_user(discord_id, minecraft_uuid)
        self._users[discord_id] = user
        return user

    async def find_user_by_discord(self, discord_id: int) -> MinecraftUser | None:
        return self._users.get(discord_id)


class _FakeRCONClient:
    instances: list[_FakeRCONClient] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.command_name: str | None = None
        self.closed = False
        self.__class__.instances.append(self)

    async def connect(self, password: str, timeout: float = 10.0) -> None:
        return None

    async def command(self, command: str, timeout: float = 15.0) -> str:
        self.command_name = command
        return "ok"

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_register_uuid_auto_whitelists_stopped_server(tmp_path: Path) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    repo._servers = [_make_server("testy", folder)]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    user = await service.register_uuid(111, _UUID_A)

    assert user.minecraft_uuid == _UUID_A
    whitelist = _read_whitelist(folder)
    assert {e["uuid"] for e in whitelist} == {_UUID_A}

    # Re-registering the same user must not duplicate the entry.
    await service.register_uuid(111, _UUID_A)
    assert len(_read_whitelist(folder)) == 1


@pytest.mark.asyncio
async def test_register_uuid_auto_whitelists_running_server_via_rcon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    server = _make_server("testy", folder)
    repo._servers = [server]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))
    service._processes[server.id] = object()  # mark the server as running

    monkeypatch.setattr(minecraft_service, "RCONClient", _FakeRCONClient)
    _FakeRCONClient.instances.clear()

    await service.register_uuid(111, _UUID_A)

    assert _FakeRCONClient.instances
    assert _FakeRCONClient.instances[-1].command_name == f"whitelist add {_UUID_A}"
    # RCON applies the change itself; the file is not rewritten by us.
    assert _read_whitelist(folder) == []


@pytest.mark.asyncio
async def test_register_uuid_whitelists_every_stopped_server(tmp_path: Path) -> None:
    folder_a = tmp_path / "alpha"
    folder_b = tmp_path / "beta"
    folder_a.mkdir()
    folder_b.mkdir()
    repo = _FakeRepo()
    repo._servers = [
        _make_server("alpha", folder_a, server_id=1),
        _make_server("beta", folder_b, server_id=2),
    ]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    await service.register_uuid(111, _UUID_A)

    assert {e["uuid"] for e in _read_whitelist(folder_a)} == {_UUID_A}
    assert {e["uuid"] for e in _read_whitelist(folder_b)} == {_UUID_A}


@pytest.mark.asyncio
async def test_whitelist_user_running_server_uses_rcon_with_uuid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    repo._users[111] = _make_user(111, _UUID_A)
    server = _make_server("testy", folder)
    repo._servers = [server]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))
    service._processes[server.id] = object()

    monkeypatch.setattr(minecraft_service, "RCONClient", _FakeRCONClient)
    _FakeRCONClient.instances.clear()

    output = await service.whitelist_user("testy", "add", 111)

    assert _FakeRCONClient.instances[-1].command_name == f"whitelist add {_UUID_A}"
    assert output == "ok"


@pytest.mark.asyncio
async def test_whitelist_user_stopped_server_edits_file(tmp_path: Path) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    (folder / "whitelist.json").write_text(
        json.dumps([{"uuid": _UUID_B, "name": ""}]), encoding="utf-8"
    )
    repo = _FakeRepo()
    repo._users[111] = _make_user(111, _UUID_A)
    repo._servers = [_make_server("testy", folder)]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    output = await service.whitelist_user("testy", "add", 111)
    assert "added" in output
    assert {e["uuid"] for e in _read_whitelist(folder)} == {_UUID_A, _UUID_B}

    # Adding again is a no-op (dedup).
    await service.whitelist_user("testy", "add", 111)
    assert len(_read_whitelist(folder)) == 2

    output = await service.whitelist_user("testy", "remove", 111)
    assert "removed" in output
    assert {e["uuid"] for e in _read_whitelist(folder)} == {_UUID_B}


@pytest.mark.asyncio
async def test_whitelist_user_requires_registered_uuid(tmp_path: Path) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    repo._servers = [_make_server("testy", folder)]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    with pytest.raises(MinecraftUnauthorized):
        await service.whitelist_user("testy", "add", 111)


@pytest.mark.asyncio
async def test_sync_all_whitelists_applies_registered_members(tmp_path: Path) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    repo._users[111] = _make_user(111, _UUID_A)
    repo._users[222] = _make_user(222, _UUID_B)
    server = _make_server("testy", folder)
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    await service._sync_all_whitelists(server)

    assert {e["uuid"] for e in _read_whitelist(folder)} == {_UUID_A, _UUID_B}


@pytest.mark.asyncio
async def test_whitelist_all_users_stopped_server_skips_existing(tmp_path: Path) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    (folder / "whitelist.json").write_text(
        json.dumps([{"uuid": _UUID_A, "name": ""}]), encoding="utf-8"
    )
    repo = _FakeRepo()
    repo._users[111] = _make_user(111, _UUID_A)
    repo._users[222] = _make_user(222, _UUID_B)
    repo._servers = [_make_server("testy", folder)]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    applied = await service.whitelist_all_users("testy")

    assert applied == 1
    assert {e["uuid"] for e in _read_whitelist(folder)} == {_UUID_A, _UUID_B}
    # Re-running is a no-op.
    assert await service.whitelist_all_users("testy") == 0


@pytest.mark.asyncio
async def test_whitelist_all_users_running_server_uses_rcon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    repo._users[111] = _make_user(111, _UUID_A)
    repo._users[222] = _make_user(222, _UUID_B)
    server = _make_server("testy", folder)
    repo._servers = [server]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))
    service._processes[server.id] = object()

    monkeypatch.setattr(minecraft_service, "RCONClient", _FakeRCONClient)
    _FakeRCONClient.instances.clear()

    applied = await service.whitelist_all_users("testy")

    assert applied == 2
    commands = [i.command_name for i in _FakeRCONClient.instances]
    assert commands == [f"whitelist add {_UUID_A}", f"whitelist add {_UUID_B}"]


@pytest.mark.asyncio
async def test_whitelist_all_users_no_registered_users(tmp_path: Path) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    repo = _FakeRepo()
    repo._servers = [_make_server("testy", folder)]
    service = minecraft_service.MinecraftService(repo, _make_settings(tmp_path))

    assert await service.whitelist_all_users("testy") == 0
    assert _read_whitelist(folder) == []
