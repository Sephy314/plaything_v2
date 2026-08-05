"""Voice service — business logic for voice settings."""

from __future__ import annotations

from core.logger import get_logger
from models.voice_dataclass import VoiceSetting
from repository.voice_repository import VoiceRepository

log = get_logger(__name__)


class VoiceService:
    """Handles voice-setting business rules."""

    def __init__(self, voice_repository: VoiceRepository) -> None:
        self._repository = voice_repository

    async def set_voice(self, discord_id: int, voice_id: str) -> VoiceSetting:
        """Persist the voice preference for a user.

        Args:
            discord_id: The Discord snowflake id.
            voice_id: The requested voice identifier.

        Returns:
            The saved :class:`VoiceSetting`.
        """
        if not voice_id.strip():
            raise ValueError("voice_id must not be empty")
        return await self._repository.upsert(discord_id, voice_id)

    async def get_voice(self, discord_id: int) -> VoiceSetting | None:
        """Return the stored voice preference, if any.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The stored :class:`VoiceSetting` or ``None``.
        """
        return await self._repository.find(discord_id)
