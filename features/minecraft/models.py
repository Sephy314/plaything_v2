"""Minecraft value objects.

Lightweight dataclasses mirroring the persisted rows. They are produced by
the repository from ``asyncpg.Record`` rows and consumed by the service and
command layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

STATUS_STOPPED = "stopped"
STATUS_RUNNING = "running"


@dataclass(frozen=True)
class MinecraftServer:
    """A managed Minecraft server record.

    Attributes:
        id: Auto-increment primary key.
        alias: Human-friendly unique name.
        folder_path: Absolute path to the server folder.
        port: Minecraft server port.
        status: ``running`` or ``stopped``.
        created_by: Discord id of the creating user.
        created_at: Row creation timestamp.
        updated_at: Row last-update timestamp.
    """

    id: int
    alias: str
    folder_path: str
    port: int
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> MinecraftServer:
        """Build a :class:`MinecraftServer` from an ``asyncpg`` record."""
        return cls(
            id=row["id"],
            alias=row["alias"],
            folder_path=row["folder_path"],
            port=row["port"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class MinecraftUser:
    """Links a Discord user to a Minecraft account.

    Attributes:
        discord_id: Discord snowflake id.
        minecraft_uuid: The player's Minecraft UUID (with dashes).
        created_at: Row creation timestamp.
    """

    discord_id: int
    minecraft_uuid: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> MinecraftUser:
        """Build a :class:`MinecraftUser` from an ``asyncpg`` record."""
        return cls(
            discord_id=row["discord_id"],
            minecraft_uuid=row["minecraft_uuid"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class MinecraftSession:
    """Tracks the last known player count for a server.

    Attributes:
        server_id: The associated server id.
        player_count: Last observed online player count.
        last_changed_at: When the player count last changed.
    """

    server_id: int
    player_count: int
    last_changed_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> MinecraftSession:
        """Build a :class:`MinecraftSession` from an ``asyncpg`` record."""
        return cls(
            server_id=row["server_id"],
            player_count=row["player_count"],
            last_changed_at=row["last_changed_at"],
        )
