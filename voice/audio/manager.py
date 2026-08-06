"""Shared audio manager for TTS and YouTube playback.

Owns a single Discord voice client, a mixer that lets TTS play over music,
and the music/TTS players. All enqueue / play / stop / skip / clear
operations are exposed here so the TTS and music features share one audio
layer without blocking each other.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Callable

from core.exceptions import PlaybackError, VoiceException
from core.logger import get_logger
from voice.audio.mixer import AudioMixer
from voice.audio.pcm import FFmpegPcmSource
from voice.audio.source import MixedAudioSource
from voice.music.player import MusicPlayer
from voice.music.youtube import YoutubeClient
from voice.tts.player import TtsPlayer
from voice.tts.provider import ProviderRegistry

log = get_logger(__name__)


class AudioManager:
    """Co-ordinates TTS and YouTube playback on one voice client.

    Args:
        voice_client: The Discord voice client to play audio on.
        ffmpeg: FFmpeg executable path/name.
        provider_registry: Registry resolving the active TTS provider.
        tts_voice_service: Service resolving per-user TTS voice settings.
        youtube: YouTube extraction client.
    """

    def __init__(
        self,
        voice_client: Any,
        *,
        ffmpeg: str = "ffmpeg",
        provider_registry: ProviderRegistry | None = None,
        tts_voice_service: Any | None = None,
        youtube: YoutubeClient | None = None,
    ) -> None:
        self._vc = voice_client
        self._ffmpeg = ffmpeg
        self._provider_registry = provider_registry or ProviderRegistry()
        self._tts_voice_service = tts_voice_service
        self._youtube = youtube or YoutubeClient()

        self._mixer = AudioMixer()
        self._source = MixedAudioSource(self._mixer)
        self._music = MusicPlayer(ffmpeg)
        self._tts = TtsPlayer()

        self._tts_pending: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._tts_worker: asyncio.Task | None = None
        self._started = False

    # ------------------------------------------------------------------
    # TTS
    # ------------------------------------------------------------------

    def speak(self, text: str, voice: str, language: str) -> None:
        """Queue text for speech synthesis and playback (non-blocking).

        Args:
            text: The text to speak.
            voice: The configured voice identifier.
            language: The detected language tag.
        """
        if not text or not text.strip():
            return
        self._tts_pending.put_nowait((text, voice, language))
        self._start_tts_worker()
        self._attach_and_start("tts")

    def _start_tts_worker(self) -> None:
        if self._tts_worker is None or self._tts_worker.done():
            loop = asyncio.get_running_loop()
            self._tts_worker = loop.create_task(self._tts_worker_loop())

    async def _tts_worker_loop(self) -> None:
        while True:
            try:
                text, voice, language = await self._tts_pending.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._synthesize(text, voice, language)
            except Exception as exc:  # provider / network / ffmpeg failures
                log.error("tts synthesis failed: %s", exc, exc_info=exc)
            finally:
                self._tts_pending.task_done()

    async def _synthesize(self, text: str, voice: str, language: str) -> None:
        provider = self._provider_registry.primary()
        audio = await provider.synthesize(text, voice, language)
        if not audio:
            raise PlaybackError("TTS provider returned empty audio")
        path = self._write_temp_audio(audio)
        source = FFmpegPcmSource(
            self._ffmpeg,
            input_path=path,
            on_close=lambda: _unlink(path),
        )
        self._tts.enqueue(source)
        self._attach_and_start("tts")
        log.info("tts queued (voice=%s lang=%s)", voice, language)

    @staticmethod
    def _write_temp_audio(audio: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, "wb") as handle:
            handle.write(audio)
        return path

    # ------------------------------------------------------------------
    # YouTube
    # ------------------------------------------------------------------

    async def play_music(
        self,
        url: str,
        *,
        loop: bool = False,
        on_track_start: Callable[[Any], None] | None = None,
    ) -> list[Any]:
        """Resolve and enqueue YouTube tracks, starting playback if needed.

        Args:
            url: A YouTube video or playlist URL.
            loop: Repeat the current track when True.
            on_track_start: Optional callback fired (from the voice playback
                thread) whenever a track actually starts playing.

        Returns:
            The resolved list of tracks.

        Raises:
            YoutubeError: If the URL cannot be resolved.
        """
        tracks = await self._youtube.extract(url)
        self._music.set_loop(loop)
        self._music.on_track_start = on_track_start
        for track in tracks:
            self._music.enqueue(track)
        self._attach_and_start("music")
        return tracks

    def skip(self) -> None:
        """Skip the currently playing YouTube track."""
        self._music.skip()

    def skip_tts(self) -> None:
        """Skip the currently playing TTS item."""
        self._tts.skip()

    # ------------------------------------------------------------------
    # Shared audio layer (spec AudioManager interface)
    # ------------------------------------------------------------------

    def enqueue(self, source: Any) -> None:
        """Add a pre-built audio source to the TTS queue."""
        self._tts.enqueue(source)

    def play(self) -> None:
        """Start streaming to the voice client."""
        self._attach_and_start(None)

    def stop(self) -> None:
        """Stop all playback, clear queues and detach sources."""
        self._stop_tts_worker()
        self._drain_tts_pending()
        self._music.close()
        self._tts.close()
        self._mixer.detach_music()
        self._mixer.detach_tts()
        self._stop_streaming()

    def clear_queue(self) -> None:
        """Clear pending TTS and queued music without stopping current output."""
        self._drain_tts_pending()
        self._music.clear()
        self._tts.clear()

    def close(self) -> None:
        """Tear down playback and release all resources."""
        self.stop()
        self._mixer.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _attach_and_start(self, channel: str | None) -> None:
        if channel == "music":
            self._mixer.attach_music(self._music)
        elif channel == "tts":
            self._mixer.attach_tts(self._tts)
        self._start_streaming()

    def _start_streaming(self) -> None:
        if self._started:
            return
        if not self._connected():
            log.warning("voice client not connected; cannot start audio")
            return
        try:
            self._vc.play(self._source)
        except Exception as exc:
            raise VoiceException(f"Failed to start voice playback: {exc}") from exc
        self._started = True
        log.info("voice playback started")

    def _stop_streaming(self) -> None:
        if self._started:
            try:
                self._vc.stop()
            except Exception:  # pragma: no cover - defensive
                pass
            self._started = False
        log.info("voice playback stopped")

    def _connected(self) -> bool:
        try:
            return bool(self._vc and self._vc.is_connected())
        except Exception:  # pragma: no cover - defensive
            return False

    def _stop_tts_worker(self) -> None:
        if self._tts_worker is not None:
            self._tts_worker.cancel()
            self._tts_worker = None

    def _drain_tts_pending(self) -> None:
        while True:
            try:
                self._tts_pending.get_nowait()
            except asyncio.QueueEmpty:
                break


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:  # pragma: no cover - already gone
        pass
    except OSError:  # pragma: no cover - defensive
        log.debug("could not remove temp audio %s", path)
