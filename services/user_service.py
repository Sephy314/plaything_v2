"""User service — owns business logic around user records."""

from __future__ import annotations

import asyncpg

from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger
from repository.user_repository import UserRepository

log = get_logger(__name__)


class UserService:
    """Handles user-related business rules."""

    def __init__(self, db: Database) -> None:
        self._repository = UserRepository(db)

    @property
    def repository(self) -> UserRepository:
        """Expose the underlying repository for feature-specific queries."""
        return self._repository

    async def ensure_user(self, discord_id: int) -> None:
        """Ensure a Discord user exists and is persisted.

        Args:
            discord_id: The Discord snowflake id.

        Raises:
            DatabaseException: If persistence fails.
        """
        try:
            await self._repository.upsert(discord_id)
        except DatabaseException as exc:
            log.error("could not ensure user %s: %s", discord_id, exc, exc_info=exc)
            raise

    async def get_or_create(self, discord_id: int) -> asyncpg.Record | None:
        """Fetch and persist a user, returning the stored record.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The stored user record.
        """
        user = await self._repository.upsert(discord_id)
        return user
