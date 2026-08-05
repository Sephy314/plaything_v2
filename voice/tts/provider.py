"""TTS provider abstraction, language detection and concrete providers.

Providers synthesize text into audio bytes. They are interchangeable behind
:class:`TTSProvider` so the TTS feature can switch engines without changing
callers. Language detection supports Korean/English with an English fallback.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

import aiohttp

from core.exceptions import VoiceException
from core.logger import get_logger

log = get_logger(__name__)

DEFAULT_VOICE = "en-US-GuyNeural"
DEFAULT_LANGUAGE = "en"

_HANGUL = re.compile(r"[\uac00-\ud7af]")
_LATIN = re.compile(r"[A-Za-z]")

_SUPPORTED_LANGUAGES = {"ko", "en"}


class TTSProvider(ABC):
    """Contract for a text-to-speech engine."""

    @abstractmethod
    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        """Synthesize text into audio bytes.

        Args:
            text: The text to speak.
            voice: The voice identifier.
            language: The language tag (e.g. ``"ko"``, ``"en"``).

        Returns:
            Encoded audio bytes (e.g. MP3).

        Raises:
            VoiceException: If synthesis fails.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a stable identifier for this provider."""


def detect_language(text: str) -> str:
    """Detect the dominant language of a sentence.

    Uses a lightweight heuristic based on Hangul vs. Latin characters.

    Args:
        text: The input sentence.

    Returns:
        ``"ko"`` if Hangul is dominant, ``"en"`` otherwise (English fallback).
    """
    hangul = len(_HANGUL.findall(text))
    latin = len(_LATIN.findall(text))
    if hangul > latin:
        return "ko"
    return "en"


def normalize_voice(voice: str, language: str) -> str:
    """Return a usable voice id, mapping ``"default"`` to a language default.

    Args:
        voice: The configured voice identifier.
        language: The detected language tag.

    Returns:
        A concrete voice identifier.
    """
    if voice and voice.lower() not in ("default", ""):
        return voice
    return "ko-KR-SunHiNeural" if language == "ko" else DEFAULT_VOICE


class EdgeTTSProvider(TTSProvider):
    """Synthesize via the free Microsoft Edge TTS service."""

    def __init__(self) -> None:
        self._module: object | None = None

    @property
    def name(self) -> str:
        return "edge-tts"

    def _get_module(self):
        if self._module is None:
            import edge_tts  # type: ignore[import-not-found]  # noqa: PLC0415

            self._module = edge_tts
        return self._module

    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        if not text or not text.strip():
            raise VoiceException("Cannot synthesize empty text")
        edge = self._get_module()
        voice_id = normalize_voice(voice, language)
        try:
            communicate = edge.Communicate(text=text, voice=voice_id)
            chunks = [chunk async for chunk in communicate.stream()]
        except Exception as exc:  # network / service errors
            raise VoiceException(f"Edge TTS synthesis failed: {exc}") from exc
        return b"".join(chunk["data"] for chunk in chunks if chunk["type"] == "audio")


class GoogleTranslateTTSProvider(TTSProvider):
    """Fallback synthesizer using the free Google Translate TTS endpoint."""

    _URL = "https://translate.google.com/translate_tts"

    @property
    def name(self) -> str:
        return "google-translate"

    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        if not text or not text.strip():
            raise VoiceException("Cannot synthesize empty text")
        params = {"ie": "UTF-8", "client": "tw-ob", "q": text, "tl": language}
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._URL, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        raise VoiceException(
                            f"Google TTS returned status {resp.status}"
                        )
                    return await resp.read()
        except aiohttp.ClientError as exc:
            raise VoiceException(f"Google TTS request failed: {exc}") from exc


class ProviderRegistry:
    """Resolve the first available :class:`TTSProvider`."""

    def __init__(self, providers: list[TTSProvider] | None = None) -> None:
        self._providers = providers or [EdgeTTSProvider(), GoogleTranslateTTSProvider()]

    @property
    def providers(self) -> list[TTSProvider]:
        """Return the ordered provider list."""
        return self._providers

    def primary(self) -> TTSProvider:
        """Return the first configured provider.

        Raises:
            VoiceException: If no providers are configured.
        """
        if not self._providers:
            raise VoiceException("No TTS provider configured")
        return self._providers[0]

    def available(self) -> list[TTSProvider]:
        """Return providers whose dependencies import successfully."""
        result: list[TTSProvider] = []
        for provider in self._providers:
            try:
                if isinstance(provider, EdgeTTSProvider):
                    provider._get_module()  # noqa: SLF001 - availability probe
                result.append(provider)
            except ImportError:
                log.debug("TTS provider %s unavailable, skipping", provider.name)
        return result
