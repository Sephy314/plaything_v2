"""User repository.

Contains SQL only — no business logic. The database pool is injected via
the constructor (dependency injection).
"""

from __future__ import annotations

import asyncpg

from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger
from models.user_dataclass import User

log = get_logger(__name__)


class UserRepository:
    """Persistence layer for :class:`User` records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(self, discord_id: int) -> User:
        """Insert a user, or return the existing one if present.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The upserted :class:`User`.

        Raises:
            DatabaseException: If the query fails.
        """
        query = """
            INSERT INTO users (discord_id)
            VALUES ($1)
            ON CONFLICT (discord_id) DO NOTHING
            RETURNING discord_id, created_at, updated_at
        """
        try:
            row = await self._db.fetchrow(query, discord_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to upsert user {discord_id}") from exc
        if row is None:
            return await self.find(discord_id)
        return User.from_row(row)

    async def find(self, discord_id: int) -> User | None:
        """Fetch a user by Discord id.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The matching :class:`User` or ``None``.

        Raises:
            DatabaseException: If the query fails.
        """
        query = "SELECT discord_id, created_at, updated_at FROM users WHERE discord_id = $1"
        try:
            row = await self._db.fetchrow(query, discord_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to find user {discord_id}") from exc
        return User.from_row(row) if row else None

    async def delete(self, discord_id: int) -> bool:
        """Delete a user by Discord id.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            True if a row was deleted.

        Raises:
            DatabaseException: If the query fails.
        """
        query = "DELETE FROM users WHERE discord_id = $1"
        try:
            result = await self._db.execute(query, discord_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to delete user {discord_id}") from exc
        return result.endswith(" 1")
