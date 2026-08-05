from __future__ import annotations

import asyncio
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.settings import Settings
from features.minecraft import rcon as minecraft_rcon
from features.minecraft.models import MinecraftServer
from features.minecraft.rcon import RCONClient
from services import minecraft_service


class _FakeRepo:
    def __init__(self, server: MinecraftServer) -> None:
        self._server = server

    async def find_server_by_alias(self, alias: str) -> MinecraftServer | None:
        return self._server if alias == self._server.alias else None


class _FakeRCONClient:
    instances: list[_FakeRCONClient] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.connected_password: str | None = None
        self.command_name: str | None = None
        self.closed = False
        self.__class__.instances.append(self)

    async def connect(self, password: str, timeout: float = 10.0) -> None:
        self.connected_password = password

    async def command(self, command: str, timeout: float = 15.0) -> str:
        self.command_name = command
        return "ok"

    async def close(self) -> None:
        self.closed = True


class _FakeSocket:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.connected_to: tuple[str, int] | None = None

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def connect(self, address: tuple[str, int]) -> None:
        self.connected_to = address

    def getsockname(self) -> tuple[str, int]:
        return ("192.168.0.42", 54321)


def _make_server(alias: str = "testy") -> MinecraftServer:
    now = datetime.now(UTC)
    return MinecraftServer(
        id=1,
        alias=alias,
        folder_path="/tmp/testy",
        port=25565,
        status="running",
        created_by=123,
        created_at=now,
        updated_at=now,
    )


def _pack_packet(request_id: int, packet_type: int, payload: bytes = b"") -> bytes:
    body = struct.pack("<ii", request_id, packet_type) + payload + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


@pytest.mark.asyncio
async def test_get_address_falls_back_to_lan_host(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
        mc_public_host="",
        mc_internal_host="",
    )
    service = minecraft_service.MinecraftService(_FakeRepo(_make_server()), settings)
    monkeypatch.setattr(minecraft_service.socket, "socket", _FakeSocket)

    address = await service.get_address("testy")

    assert address == "192.168.0.42:25565"


def test_settings_accepts_external_host_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MC_EXTERNAL_HOST", "203.0.113.10")
    settings = Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
    )

    assert settings.mc_public_host == "203.0.113.10"


@pytest.mark.asyncio
async def test_rcon_exec_uses_server_properties_and_password_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    (folder / "server.properties").write_text(
        "server-port=25565\nrcon.port=10010\nrcon.password=abc123\n",
        encoding="utf-8",
    )
    (folder / ".rcon_password").write_text("file-secret\n", encoding="utf-8")

    server = MinecraftServer(
        id=1,
        alias="testy",
        folder_path=str(folder),
        port=25565,
        status="running",
        created_by=123,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    settings = Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
    )
    service = minecraft_service.MinecraftService(_FakeRepo(server), settings)
    monkeypatch.setattr(minecraft_service, "RCONClient", _FakeRCONClient)

    result = await service._rcon_exec(server, "list")

    assert result == "ok"
    assert _FakeRCONClient.instances[-1].host == "127.0.0.1"
    assert _FakeRCONClient.instances[-1].port == 10010
    assert _FakeRCONClient.instances[-1].connected_password == "file-secret"
    assert _FakeRCONClient.instances[-1].command_name == "list"
    assert _FakeRCONClient.instances[-1].closed is True


@pytest.mark.asyncio
async def test_ensure_runtime_files_creates_missing_server_properties(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "testy"
    folder.mkdir()
    (folder / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    server = MinecraftServer(
        id=1,
        alias="testy",
        folder_path=str(folder),
        port=25565,
        status="stopped",
        created_by=123,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    settings = Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
    )
    service = minecraft_service.MinecraftService(_FakeRepo(server), settings)

    async def _no_download(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(service, "_download_server_jar", _no_download)

    await service._ensure_runtime_files(server, folder)

    props = (folder / "server.properties").read_text(encoding="utf-8")
    assert "server-port=25565" in props
    assert "rcon.port=25575" in props
    assert (folder / ".rcon_password").read_text(encoding="utf-8").strip() == "3761783747bd7663"


@pytest.mark.asyncio
async def test_ensure_runtime_files_disables_spark_profiler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "testy"
    (folder / "config").mkdir(parents=True)
    (folder / "config" / "paper-global.yml").write_text(
        "spark:\n  enable-immediately: true\n  enabled: true\n",
        encoding="utf-8",
    )

    server = MinecraftServer(
        id=1,
        alias="testy",
        folder_path=str(folder),
        port=25565,
        status="stopped",
        created_by=123,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    settings = Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
    )
    service = minecraft_service.MinecraftService(_FakeRepo(server), settings)

    async def _no_download(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(service, "_download_server_jar", _no_download)

    await service._ensure_runtime_files(server, folder)

    paper_global = (folder / "config" / "paper-global.yml").read_text(encoding="utf-8")
    assert "enable-immediately: false" in paper_global
    assert "enabled: false" in paper_global


@pytest.mark.asyncio
async def test_rcon_auth_tolerates_extra_response_packet() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw_length = await reader.readexactly(4)
            length = struct.unpack("<i", raw_length)[0]
            body = await reader.readexactly(length)
            request_id, packet_type = struct.unpack("<ii", body[:8])
            assert packet_type == minecraft_rcon.SERVERDATA_AUTH

            writer.write(_pack_packet(request_id, minecraft_rcon.SERVERDATA_RESPONSE_VALUE))
            writer.write(_pack_packet(request_id, minecraft_rcon.SERVERDATA_AUTH_RESPONSE))
            await writer.drain()

            raw_length = await reader.readexactly(4)
            length = struct.unpack("<i", raw_length)[0]
            body = await reader.readexactly(length)
            command_id, packet_type = struct.unpack("<ii", body[:8])
            assert packet_type == minecraft_rcon.SERVERDATA_EXECCOMMAND

            writer.write(
                _pack_packet(command_id, minecraft_rcon.SERVERDATA_RESPONSE_VALUE, b"hello")
            )
            writer.write(_pack_packet(command_id, minecraft_rcon.SERVERDATA_RESPONSE_VALUE))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]

    try:
        client = RCONClient(host, port)
        await client.connect("secret", timeout=1)
        output = await client.command("list", timeout=1)
        assert output == "hello"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_read_logs_returns_last_lines() -> None:
    """Test that read_logs returns the last N lines from latest.log."""
    folder = Path("/tmp/test_logs_server")
    folder.mkdir(exist_ok=True)
    
    # Create a log file with multiple lines
    log_file = folder / "latest.log"
    log_content = "\n".join([f"Line {i}" for i in range(1, 21)])  # 20 lines
    log_file.write_text(log_content, encoding="utf-8")
    
    try:
        server = MinecraftServer(
            id=1,
            alias="test_server",
            folder_path=str(folder),
            port=25565,
            status="stopped",
            created_by=123,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        settings = Settings(
            _env_file=None,
            database_dsn="postgresql://user:pass@localhost/db",
            discord_token="token",
        )
        service = minecraft_service.MinecraftService(_FakeRepo(server), settings)
        
        logs = await service.read_logs("test_server", lines=5)
        
        # Should return the last 5 lines
        assert "Line 16" in logs
        assert "Line 20" in logs
        assert "Line 15" not in logs
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.asyncio
async def test_read_logs_handles_missing_file() -> None:
    """Test that read_logs handles missing log files gracefully."""
    folder = Path("/tmp/test_logs_missing")
    folder.mkdir(exist_ok=True)
    
    try:
        server = MinecraftServer(
            id=1,
            alias="test_server",
            folder_path=str(folder),
            port=25565,
            status="stopped",
            created_by=123,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        settings = Settings(
            _env_file=None,
            database_dsn="postgresql://user:pass@localhost/db",
            discord_token="token",
        )
        service = minecraft_service.MinecraftService(_FakeRepo(server), settings)
        
        logs = await service.read_logs("test_server")
        
        # Should return a friendly message for missing logs
        assert "(로그 파일이 없습니다)" in logs
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.asyncio
async def test_list_servers_returns_all_servers() -> None:
    """Test that list_servers returns all registered servers."""
    server1 = MinecraftServer(
        id=1,
        alias="survival",
        folder_path="/tmp/survival",
        port=25565,
        status="running",
        created_by=123,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    server2 = MinecraftServer(
        id=2,
        alias="creative",
        folder_path="/tmp/creative",
        port=25566,
        status="stopped",
        created_by=123,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    
    class _MultiServerRepo(_FakeRepo):
        def __init__(self) -> None:
            pass
        
        async def list_servers(self) -> list[MinecraftServer]:
            return [server1, server2]
    
    settings = Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
    )
    service = minecraft_service.MinecraftService(_MultiServerRepo(), settings)
    
    servers = await service.list_servers()
    
    assert len(servers) == 2
    assert servers[0].alias == "survival"
    assert servers[1].alias == "creative"
