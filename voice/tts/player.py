"""TTS player: plays a queue of pre-synthesized audio sources sequentially.

Synthesis happens asynchronously upstream; this player only decodes already
prepared :class:`PcmFrameSource` items in FIFO order so multiple users'
messages are spoken in arrival order.
"""

from __future__ import annotations

from collections import deque

from core.logger import get_logger
from voice.audio.pcm import PcmFrameSource

log = get_logger(__name__)


class TtsPlayer:
    """Plays a FIFO queue of PCM audio sources."""

    def __init__(self) -> None:
        self._queue: deque[PcmFrameSource] = deque()
        self._current: PcmFrameSource | None = None

    def enqueue(self, source: PcmFrameSource) -> None:
        """Add a synthesized audio source to the end of the queue."""
        self._queue.append(source)
        log.debug("queued tts source (%d pending)", len(self._queue))

    def skip(self) -> None:
        """Skip the currently playing TTS item."""
        self._close_current()

    def clear(self) -> None:
        """Drop all queued (not yet started) sources."""
        while self._queue:
            source = self._queue.popleft()
            try:
                source.close()
            except Exception:  # pragma: no cover - defensive
                pass

    @property
    def queue_size(self) -> int:
        """Number of queued (not yet started) sources."""
        return len(self._queue)

    def is_playing(self) -> bool:
        """Whether a TTS item is currently being decoded."""
        return self._current is not None

    def read_frame(self) -> bytes | None:
        """Return the next PCM frame, advancing between queued items."""
        if self._current is None:
            self._start_next()
            if self._current is None:
                return None
        frame = self._current.read_frame()
        if frame is not None:
            return frame
        self._close_current()
        self._start_next()
        if self._current is None:
            return None
        return self.read_frame()

    def _start_next(self) -> None:
        if self._queue:
            self._current = self._queue.popleft()

    def _close_current(self) -> None:
        if self._current is not None:
            try:
                self._current.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self._current = None

    def close(self) -> None:
        """Release the current source and drop the queue."""
        self._close_current()
        self.clear()
