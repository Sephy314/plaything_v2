"""TTS voice service — business logic for TTS voice settings."""

from __future__ import annotations

from core.logger import get_logger
from models.tts_voice_dataclass import TTSVoiceSetting
from repository.tts_voice_repository import TTSVoiceRepository

log = get_logger(__name__)

DEFAULT_VOICE = "default"
DEFAULT_LANGUAGE = "en"

# Available voices for different languages
AVAILABLE_VOICES = {
    "en": [
        "en-US-GuyNeural",
        "en-US-AriaNeural",
        "en-GB-RyanNeural",
        "en-AU-WilliamNeural",
        "en-CA-ClaudeNeural",
    ],
    "ko": [
        "ko-KR-SunHiNeural",
        "ko-KR-InJoonNeural",
        "ko-KR-BongJinNeural",
    ],
    "default": ["default"],
}


class TTSVoiceService:
    """Handles TTS voice-setting business rules."""

    def __init__(self, repository: TTSVoiceRepository) -> None:
        self._repository = repository

    def list_voices(self) -> list[str]:
        """Return all available voice IDs.
        
        Returns:
            List of voice identifiers including language-specific and default.
        """
        voices = []
        # Add default first
        voices.extend(AVAILABLE_VOICES.get("default", []))
        # Then add language-specific voices
        for lang in ["en", "ko"]:
            voices.extend(AVAILABLE_VOICES.get(lang, []))
        return voices

    async def set_voice(self, discord_id: int, voice_id: str) -> TTSVoiceSetting:
        """Persist the TTS voice preference for a user.

        Args:
            discord_id: The Discord snowflake id.
            voice_id: The requested voice identifier.

        Returns:
            The saved :class:`TTSVoiceSetting`.
        """
        if not voice_id or not voice_id.strip():
            raise ValueError("voice_id must not be empty")
        return await self._repository.upsert(discord_id, voice_id.strip(), DEFAULT_LANGUAGE)

    async def set_language(self, discord_id: int, language: str) -> TTSVoiceSetting | None:
        """Persist the detected language tag for a user.

        Args:
            discord_id: The Discord snowflake id.
            language: The detected language tag.

        Returns:
            The updated :class:`TTSVoiceSetting` or ``None``.
        """
        return await self._repository.update_language(discord_id, language)

    async def get_voice(self, discord_id: int) -> TTSVoiceSetting | None:
        """Return the stored TTS voice preference, if any.

        Args:
            discord_id: The Discord snowflake id.

        Returns:
            The stored :class:`TTSVoiceSetting` or ``None``.
        """
        return await self._repository.find(discord_id)
