"""Voice setting value object(s)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VoiceSetting:
    """Per-user voice configuration.

    Attributes:
        discord_id: Discord snowflake id.
        voice_id: The configured voice identifier (e.g. a TTS voice).
    """

    discord_id: int
    voice_id: str

    @classmethod
    def from_row(cls, row: Any) -> VoiceSetting:
        """Build a :class:`VoiceSetting` from an ``asyncpg`` record.

        Args:
            row: The database row.

        Returns:
            A populated :class:`VoiceSetting`.
        """
        return cls(discord_id=row["discord_id"], voice_id=row["voice_id"])
