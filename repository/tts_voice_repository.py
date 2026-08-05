"""TTS voice settings repository — SQL only."""

from __future__ import annotations

import asyncpg

from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger
from models.tts_voice_dataclass import TTSVoiceSetting

log = get_logger(__name__)


class TTSVoiceRepository:
    """Persistence layer for :class:`TTSVoiceSetting` records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(
        self,
        discord_id: int,
        voice_id: str,
        language: str = "en",
    ) -> TTSVoiceSetting:
        """Insert or update a TTS voice setting.

        Args:
            discord_id: The Discord snowflake id.
            voice_id: The configured voice identifier.
            language: The detected/stored language tag.

        Returns:
            The saved :class:`TTSVoiceSetting`.

        Raises:
            DatabaseException: If the query fails.
        """
        query = """
            INSERT INTO tts_voice_settings (discord_id, voice_id, language)
            VALUES ($1, $2, $3)
            ON CONFLICT (discord_id)
            DO UPDATE SET voice_id = EXCLUDED.voice_id, language = EXCLUDED.language
            RETURNING discord_id, voice_id, language, created_at, updated_at
        """
        try:
            row = await self._db.fetchrow(query, discord_id, voice_id, language)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to upsert tts setting for {discord_id}") from exc
        return TTSVoiceSetting.from_row(row)

    async def update_language(self, discord_id: int, language: str) -> TTSVoiceSetting | None:
        """Update only the stored language tag for a user.

        Args:
            discord_id: The Discord snowflake id.
            language: The detected language tag.

        Returns:
            The updated :class:`TTSVoiceSetting` or ``None`` if absent.
        """
        query = """
            UPDATE tts_voice_settings SET language = $2 WHERE discord_id = $1
            RETURNING discord_id, voice_id, language, created_at, updated_at
        """
        try:
            row = await self._db.fetchrow(query, discord_id, language)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to update tts language for {discord_id}") from exc
        return TTSVoiceSetting.from_row(row) if row else None

    async def find(self, discord_id: int) -> TTSVoiceSetting | None:
        """Fetch the TTS voice setting for a user.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The matching :class:`TTSVoiceSetting` or ``None``.
        """
        query = (
            "SELECT discord_id, voice_id, language, created_at, updated_at "
            "FROM tts_voice_settings WHERE discord_id = $1"
        )
        try:
            row = await self._db.fetchrow(query, discord_id)
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Failed to find tts setting for {discord_id}") from exc
        return TTSVoiceSetting.from_row(row) if row else None
