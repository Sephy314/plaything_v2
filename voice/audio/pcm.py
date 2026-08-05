"""PCM frame source abstraction and FFmpeg-backed implementation.

A :class:`PcmFrameSource` yields fixed-size raw PCM frames (16-bit signed
little-endian, 48 kHz, stereo). Each frame is exactly
``FRAME_BYTES = 3840`` bytes (20 ms of audio), which is what discord.py
expects before its internal Opus encoder.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

from core.exceptions import VoiceException
from core.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2
FRAME_SAMPLES = 960  # 20 ms at 48 kHz
FRAME_BYTES = FRAME_SAMPLES * CHANNELS * BYTES_PER_SAMPLE  # 3840


def silence_frame() -> bytes:
    """Return a frame of digital silence.

    Returns:
        A zeroed PCM frame of :data:`FRAME_BYTES` length.
    """
    return b"\x00" * FRAME_BYTES


class PcmFrameSource(Protocol):
    """Synchronous producer of PCM frames.

    Implementations are invoked from the Discord voice playback thread and
    must be non-blocking-or-fast and thread-safe.
    """

    def read_frame(self) -> bytes | None:
        """Return the next PCM frame or ``None`` when exhausted."""

    def close(self) -> None:
        """Release any underlying resources."""


class FFmpegPcmSource:
    """Decode an input media into PCM via a spawned FFmpeg subprocess.

    Args:
        executable: Path/name of the FFmpeg binary.
        input_args: Extra arguments placed before the ``-i`` input (e.g. network
            reconnect options).
        input_path: The media input (URL or local path).
        extra_options: Additional output options appended before ``pipe:1``.
    """

    def __init__(
        self,
        executable: str,
        input_args: list[str] | None = None,
        input_path: str = "-",
        extra_options: list[str] | None = None,
        on_close=None,
    ) -> None:
        self._executable = executable
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._on_close = on_close
        args = [
            executable,
            "-v",
            "error",
            *(input_args or []),
            "-i",
            input_path,
            "-vn",
            "-ac",
            str(CHANNELS),
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            *(extra_options or []),
            "pipe:1",
        ]
        try:
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise VoiceException(f"Failed to launch ffmpeg: {exc}") from exc
        log.debug("ffmpeg started for %r", input_path)

    def read_frame(self) -> bytes | None:
        """Read a single PCM frame, or ``None`` at end of stream."""
        if self._closed or self._process is None or self._process.stdout is None:
            return None
        data = self._process.stdout.read(FRAME_BYTES)
        if len(data) < FRAME_BYTES:
            return None
        return data

    def close(self) -> None:
        """Terminate the FFmpeg subprocess if still running."""
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:  # pragma: no cover - defensive
                try:
                    process.kill()
                except Exception:  # pragma: no cover - defensive
                    pass
        else:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:  # pragma: no cover - defensive
                    pass
        self._process = None
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:  # pragma: no cover - defensive
                pass
        log.debug("ffmpeg closed")
