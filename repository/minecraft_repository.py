"""Minecraft repository — SQL only.

Provides persistence for Minecraft servers, the Discord↔UUID mapping, and
per-server player sessions. No business logic lives here; the service layer
owns the rules.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger
from features.minecraft.models import (
    MinecraftServer,
    MinecraftSession,
    MinecraftUser,
)

log = get_logger(__name__)

_SERVER_COLUMNS = "id, alias, folder_path, port, status, created_by, created_at, updated_at"


class MinecraftRepository:
    """Persistence layer for Minecraft server / user / session records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- Server CRUD ----------------------------------------------------

    async def list_servers(self) -> list[MinecraftServer]:
        """Return all managed servers ordered by alias."""
        query = f"SELECT {_SERVER_COLUMNS} FROM minecraft_servers ORDER BY alias"
        return await self._fetch_servers(query)

    async def list_running_servers(self) -> list[MinecraftServer]:
        """Return all servers currently marked as running."""
        query = (
            f"SELECT {_SERVER_COLUMNS} FROM minecraft_servers "
            "WHERE status = 'running' ORDER BY alias"
        )
        return await self._fetch_servers(query)

    async def find_server(self, server_id: int) -> MinecraftServer | None:
        """Fetch a server by its primary key."""
        query = f"SELECT {_SERVER_COLUMNS} FROM minecraft_servers WHERE id = $1"
        return await self._fetch_server(query, server_id)

    async def find_server_by_alias(self, alias: str) -> MinecraftServer | None:
        """Fetch a server by its unique alias."""
        query = f"SELECT {_SERVER_COLUMNS} FROM minecraft_servers WHERE alias = $1"
        return await self._fetch_server(query, alias)

    async def find_server_by_port(self, port: int) -> MinecraftServer | None:
        """Fetch a server listening on the given Minecraft port."""
        query = f"SELECT {_SERVER_COLUMNS} FROM minecraft_servers WHERE port = $1"
        return await self._fetch_server(query, port)

    async def register_server(
        self,
        alias: str,
        folder_path: str,
        port: int,
        created_by: int,
        conn: Any | None = None,
    ) -> MinecraftServer:
        """Insert a new server, returning the created record.

        Args:
            alias: Unique server alias.
            folder_path: Absolute folder path.
            port: Server port.
            created_by: Discord id of the creator.
            conn: Optional connection (inside an external transaction).

        Raises:
            DatabaseException: If the insert fails (e.g. duplicate alias).
        """
        query = f"""
            INSERT INTO minecraft_servers (alias, folder_path, port, status, created_by)
            VALUES ($1, $2, $3, 'stopped', $4)
            RETURNING {_SERVER_COLUMNS}
        """
        return await self._insert_server(query, alias, folder_path, port, created_by, conn)

    async def set_status(self, server_id: int, status: str) -> None:
        """Persist a server's running status."""
        query = "UPDATE minecraft_servers SET status = $1, updated_at = now() WHERE id = $2"
        try:
            await self._db.execute(query, status, server_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to set status for server {server_id}") from exc

    async def delete_server(self, server_id: int) -> bool:
        """Remove a server record and cascade dependent rows.

        Returns:
            True if a row was deleted.
        """
        query = "DELETE FROM minecraft_servers WHERE id = $1"
        try:
            result = await self._db.execute(query, server_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to delete server {server_id}") from exc
        return result.endswith(" 1")

    # ---- UUID mapping ---------------------------------------------------

    async def upsert_user(self, discord_id: int, minecraft_uuid: str) -> MinecraftUser:
        """Insert or replace the Discord↔UUID mapping for a user."""
        query = """
            INSERT INTO minecraft_users (discord_id, minecraft_uuid)
            VALUES ($1, $2)
            ON CONFLICT (discord_id)
            DO UPDATE SET minecraft_uuid = EXCLUDED.minecraft_uuid,
                          created_at = now()
            RETURNING discord_id, minecraft_uuid, created_at
        """
        return await self._insert_user(query, discord_id, minecraft_uuid)

    async def find_user_by_discord(self, discord_id: int) -> MinecraftUser | None:
        """Fetch a Minecraft mapping for a Discord id."""
        query = (
            "SELECT discord_id, minecraft_uuid, created_at FROM minecraft_users "
            "WHERE discord_id = $1"
        )
        return await self._fetch_user(query, discord_id)

    async def find_user_by_uuid(self, minecraft_uuid: str) -> MinecraftUser | None:
        """Fetch the Discord mapping owning the given UUID."""
        query = (
            "SELECT discord_id, minecraft_uuid, created_at FROM minecraft_users "
            "WHERE minecraft_uuid = $1"
        )
        return await self._fetch_user(query, minecraft_uuid)

    # ---- Player sessions -------------------------------------------------

    async def upsert_session(self, server_id: int, player_count: int) -> MinecraftSession:
        """Upsert the player count for a server.

        ``last_changed_at`` is only refreshed when the count differs from the
        stored value so auto-shutdown timers can key off real changes.

        Args:
            server_id: The server id.
            player_count: The observed online player count.
        """
        query = """
            INSERT INTO minecraft_sessions (server_id, player_count, last_changed_at)
            VALUES ($1, $2, now())
            ON CONFLICT (server_id)
            DO UPDATE SET
                player_count = EXCLUDED.player_count,
                last_changed_at = CASE
                    WHEN minecraft_sessions.player_count = EXCLUDED.player_count
                    THEN minecraft_sessions.last_changed_at
                    ELSE now()
                END
            RETURNING server_id, player_count, last_changed_at
        """
        try:
            row = await self._db.fetchrow(query, server_id, player_count)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to upsert session for server {server_id}") from exc
        return MinecraftSession.from_row(row)

    async def get_session(self, server_id: int) -> MinecraftSession | None:
        """Fetch the stored session for a server, if present."""
        query = (
            "SELECT server_id, player_count, last_changed_at FROM minecraft_sessions "
            "WHERE server_id = $1"
        )
        try:
            row = await self._db.fetchrow(query, server_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to fetch session for server {server_id}") from exc
        return MinecraftSession.from_row(row) if row else None

    # ---- helpers ---------------------------------------------------------

    async def _fetch_servers(self, query: str) -> list[MinecraftServer]:
        try:
            rows = await self._db.fetch(query)
        except asyncpg.PostgresError as exc:
            raise DatabaseException("Failed to list minecraft servers") from exc
        return [MinecraftServer.from_row(row) for row in rows]

    async def _fetch_server(self, query: str, *args: Any) -> MinecraftServer | None:
        try:
            row = await self._db.fetchrow(query, *args)
        except asyncpg.PostgresError as exc:
            raise DatabaseException("Failed to fetch minecraft server") from exc
        return MinecraftServer.from_row(row) if row else None

    async def _insert_server(
        self, query: str, alias: str, folder_path: str, port: int, created_by: int, conn: Any
    ) -> MinecraftServer:
        try:
            if conn is not None:
                row = await conn.fetchrow(query, alias, folder_path, port, created_by)
            else:
                row = await self._db.fetchrow(query, alias, folder_path, port, created_by)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to register server {alias}") from exc
        return MinecraftServer.from_row(row)

    async def _insert_user(self, query: str, discord_id: int, minecraft_uuid: str) -> MinecraftUser:
        try:
            row = await self._db.fetchrow(query, discord_id, minecraft_uuid)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to upsert minecraft user {discord_id}") from exc
        return MinecraftUser.from_row(row)

    async def _fetch_user(self, query: str, *args: Any) -> MinecraftUser | None:
        try:
            row = await self._db.fetchrow(query, *args)
        except asyncpg.PostgresError as exc:
            raise DatabaseException("Failed to fetch minecraft user") from exc
        return MinecraftUser.from_row(row) if row else None
