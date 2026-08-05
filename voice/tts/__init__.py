"""TTS subsystem: providers, player and manager glue."""

from __future__ import annotations

from voice.tts.provider import (
    ProviderRegistry,
    TTSProvider,
    detect_language,
    normalize_voice,
)
from voice.tts.player import TtsPlayer

__all__ = [
    "ProviderRegistry",
    "TTSProvider",
    "TtsPlayer",
    "detect_language",
    "normalize_voice",
]
