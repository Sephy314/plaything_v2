"""PCM mixing utilities.

Provides the pure mixing math (:func:`mix_pcm`) and the runtime mixer that
combines a music source and a TTS source into a single output stream so TTS
can play over YouTube music without interrupting it.
"""

from __future__ import annotations

from typing import Any

from voice.audio.pcm import FRAME_BYTES, PcmFrameSource, silence_frame

_HEADROOM = 0.7
_clamp_max = 32767.0
_clamp_min = -32768.0


def mix_pcm(music: bytes | None, tts: bytes | None) -> bytes:
    """Mix two PCM frames (or ``None`` meaning silence) into one frame.

    Each input frame is ``FRAME_BYTES`` long. Samples are summed with a
    fixed headroom scaling to reduce clipping, then clamped to the 16-bit
    signed range.

    Args:
        music: Music PCM frame or ``None``.
        tts: TTS PCM frame or ``None``.

    Returns:
        A mixed PCM frame of length :data:`FRAME_BYTES`.
    """
    if music is None and tts is None:
        return silence_frame()
    if music is None:
        return tts if len(tts) == FRAME_BYTES else silence_frame()
    if tts is None:
        return music if len(music) == FRAME_BYTES else silence_frame()

    a = memoryview(music).cast("h")
    b = memoryview(tts).cast("h")
    n = min(len(a), len(b))
    out = bytearray(FRAME_BYTES)
    result = memoryview(out).cast("h")
    for i in range(n):
        value = (a[i] * _HEADROOM) + (b[i] * _HEADROOM)
        if value > _clamp_max:
            value = _clamp_max
        elif value < _clamp_min:
            value = _clamp_min
        result[i] = int(value)
    return bytes(out)


class AudioMixer:
    """Runtime mixer holding an optional music and TTS PCM source.

    This is called synchronously from the Discord voice playback thread, so
    reads are kept simple and thread-safe by only reading one frame at a
    time from each attached source.

    Attributes:
        music: The active music :class:`PcmFrameSource`, if any.
        tts: The active TTS :class:`PcmFrameSource`, if any.
    """

    def __init__(self) -> None:
        self.music: PcmFrameSource | None = None
        self.tts: PcmFrameSource | None = None

    def attach_music(self, source: Any) -> None:
        """Replace the active music source (old one is closed)."""
        self._replace("music", source)

    def attach_tts(self, source: Any) -> None:
        """Replace the active TTS source (old one is closed)."""
        self._replace("tts", source)

    def detach_music(self) -> None:
        """Stop and release the music source."""
        self._replace("music", None)

    def detach_tts(self) -> None:
        """Stop and release the TTS source."""
        self._replace("tts", None)

    def _replace(self, attr: str, source: Any) -> None:
        current = getattr(self, attr)
        if current is not None and current is not source:
            try:
                current.close()
            except Exception:  # pragma: no cover - defensive
                pass
        setattr(self, attr, source)

    def next_frame(self) -> bytes:
        """Return the next mixed PCM frame.

        Reads one frame from each active source and mixes them. If both are
        silent/exhausted, a silence frame is returned so playback continues.

        Returns:
            A mixed PCM frame.
        """
        music = self.music.read_frame() if self.music is not None else None
        tts = self.tts.read_frame() if self.tts is not None else None
        return mix_pcm(music, tts)

    def close(self) -> None:
        """Release both attached sources."""
        for attr in ("music", "tts"):
            source = getattr(self, attr)
            if source is not None:
                try:
                    source.close()
                except Exception:  # pragma: no cover - defensive
                    pass
            setattr(self, attr, None)
