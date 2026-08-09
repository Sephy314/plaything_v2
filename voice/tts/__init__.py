"""TTS subsystem: providers, player and manager glue."""

from __future__ import annotations

from voice.tts.player import TtsPlayer
from voice.tts.provider import (
    ProviderRegistry,
    TTSProvider,
    detect_language,
    normalize_voice,
)

__all__ = [
    "ProviderRegistry",
    "TTSProvider",
    "TtsPlayer",
    "detect_language",
    "normalize_voice",
]
