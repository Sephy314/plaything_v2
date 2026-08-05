"""Voice settings repository — SQL only."""

from __future__ import annotations

import asyncpg

from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger
from models.voice_dataclass import VoiceSetting

log = get_logger(__name__)


class VoiceRepository:
    """Persistence layer for :class:`VoiceSetting` records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(self, discord_id: int, voice_id: str) -> VoiceSetting:
        """Insert or update a voice setting.

        Args:
            discord_id: The Discord snowflake id.
            voice_id: The configured voice identifier.

        Returns:
            The saved :class:`VoiceSetting`.

        Raises:
            DatabaseException: If the query fails.
        """
        query = """
            INSERT INTO voice_settings (discord_id, voice_id)
            VALUES ($1, $2)
            ON CONFLICT (discord_id)
            DO UPDATE SET voice_id = EXCLUDED.voice_id
            RETURNING discord_id, voice_id
        """
        try:
            row = await self._db.fetchrow(query, discord_id, voice_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to upsert voice setting for {discord_id}") from exc
        return VoiceSetting.from_row(row)

    async def find(self, discord_id: int) -> VoiceSetting | None:
        """Fetch the voice setting for a user.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The matching :class:`VoiceSetting` or ``None``.
        """
        query = "SELECT discord_id, voice_id FROM voice_settings WHERE discord_id = $1"
        try:
            row = await self._db.fetchrow(query, discord_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to find voice setting for {discord_id}") from exc
        return VoiceSetting.from_row(row) if row else None
