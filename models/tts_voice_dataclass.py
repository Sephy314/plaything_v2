"""TTS voice setting value object."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TTSVoiceSetting:
    """Per-user TTS configuration.

    Attributes:
        discord_id: Discord snowflake id.
        voice_id: The configured TTS voice identifier.
        language: The stored/last language tag (e.g. ``"ko"``, ``"en"``).
        created_at: When the record was created.
        updated_at: When the record was last updated.
    """

    discord_id: int
    voice_id: str
    language: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> TTSVoiceSetting:
        """Build a :class:`TTSVoiceSetting` from an ``asyncpg`` record.

        Args:
            row: The database row.

        Returns:
            A populated :class:`TTSVoiceSetting`.
        """
        return cls(
            discord_id=row["discord_id"],
            voice_id=row["voice_id"],
            language=row["language"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
