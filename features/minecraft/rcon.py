"""Minimal asyncio RCON client for Minecraft servers.

Implements the source-engine Server RCON protocol used by Minecraft:

* Packet = int32 ``length`` + ``request_id`` + ``type`` + ``payload`` + nulls.
* Authentication via ``SERVERDATA_AUTH`` before issuing commands.

No third-party RCON dependency is required.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

_MAX_PACKET_LENGTH = 4096


class RCONError(Exception):
    """Base error for all RCON operations."""


class RCONConnectionError(RCONError):
    """Raised when the RCON connection could not be established."""


class RCONAuthError(RCONError):
    """Raised when RCON authentication fails."""


class RCONCommandError(RCONError):
    """Raised when a command could not be executed."""


class RCONClient:
    """Asynchronous RCON client bound to a single server connection."""

    def __init__(self, host: str = "127.0.0.1", port: int = 25575) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._authenticated = False

    async def connect(self, password: str, timeout: float = 10.0) -> None:
        """Open a TCP connection and authenticate.

        Args:
            password: The RCON password configured on the server.
            timeout: Connection / authentication timeout in seconds.

        Raises:
            RCONConnectionError: If the socket cannot be opened.
            RCONAuthError: If authentication is rejected.
        """
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=timeout,
            )
        except (TimeoutError, OSError) as exc:
            raise RCONConnectionError(
                f"Failed to connect to RCON at {self._host}:{self._port}: {exc}"
            ) from exc

        try:
            await self._authenticate(password, timeout)
        except RCONError:
            await self.close()
            raise

    async def _authenticate(self, password: str, timeout: float) -> None:
        request_id = self._next_request_id()
        await self._send(SERVERDATA_AUTH, password.encode("utf-8"), request_id)
        try:
            response_id, packet_type, _ = await asyncio.wait_for(
                self._recv_packet(), timeout=timeout
            )
        except (TimeoutError, RCONError) as exc:
            raise RCONAuthError("Timed out waiting for RCON auth response") from exc

        if response_id == -1 or packet_type != SERVERDATA_AUTH_RESPONSE:
            raise RCONAuthError("RCON authentication rejected (bad password)")
        self._authenticated = True

    async def command(self, command: str, timeout: float = 15.0) -> str:
        """Execute a server command and return its console output.

        Args:
            command: The command string, e.g. ``"list"`` or ``"say hi"``.
            timeout: Response timeout in seconds.

        Returns:
            The concatenated response payload (trailing newlines stripped).

        Raises:
            RCONCommandError: If not authenticated or the command fails.
        """
        if not self._authenticated or self._writer is None:
            raise RCONCommandError("RCON client is not authenticated")
        request_id = self._next_request_id()
        await self._send(SERVERDATA_EXECCOMMAND, command.encode("utf-8"), request_id)

        chunks: list[bytes] = []
        try:
            while True:
                response_id, packet_type, payload = await asyncio.wait_for(
                    self._recv_packet(), timeout=timeout
                )
                if packet_type != SERVERDATA_RESPONSE_VALUE or response_id != request_id:
                    continue
                chunks.append(payload)
                if not payload:
                    break
        except (TimeoutError, RCONError) as exc:
            raise RCONCommandError(f"RCON command {command!r} failed: {exc}") from exc

        return b"".join(chunks).decode("utf-8", errors="replace").strip("\n")

    async def close(self) -> None:
        """Close the underlying socket, ignoring any pending errors."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (OSError, RuntimeError):
                pass
        self._reader = None
        self._writer = None
        self._authenticated = False

    async def __aenter__(self) -> RCONClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send(self, packet_type: int, payload: bytes, request_id: int) -> None:
        if self._writer is None:
            raise RCONConnectionError("RCON client is not connected")
        body = struct.pack("<ii", request_id, packet_type) + payload + b"\x00\x00"
        packet = struct.pack("<i", len(body)) + body
        try:
            self._writer.write(packet)
            await self._writer.drain()
        except OSError as exc:
            raise RCONConnectionError(f"Failed to send RCON packet: {exc}") from exc

    async def _recv_packet(self) -> tuple[int, int, bytes]:
        if self._reader is None:
            raise RCONConnectionError("RCON client is not connected")
        try:
            raw_length = await self._reader.readexactly(4)
        except (asyncio.IncompleteReadError, OSError) as exc:
            raise RCONConnectionError(f"RCON connection closed: {exc}") from exc
        payload_length = struct.unpack("<i", raw_length)[0]
        if payload_length < 8 or payload_length > _MAX_PACKET_LENGTH:
            raise RCONConnectionError(f"Invalid RCON payload length {payload_length}")
        try:
            body = await self._reader.readexactly(payload_length)
        except (asyncio.IncompleteReadError, OSError) as exc:
            raise RCONConnectionError(f"RCON connection closed: {exc}") from exc
        request_id, packet_type = struct.unpack("<ii", body[:8])
        return request_id, packet_type, body[8 : payload_length - 2]
