"""YouTube music player: queue, looping and automatic advancement.

The player owns a FIFO queue of :class:`Track` items and a single active
:class:`FFmpegPcmSource`. It is consumed synchronously from the Discord
voice playback thread via :meth:`read_frame`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from core.logger import get_logger
from voice.audio.pcm import FFmpegPcmSource, PcmFrameSource

log = get_logger(__name__)

NETWORK_INPUT_ARGS = [
    "-reconnect",
    "1",
    "-reconnect_streamed",
    "1",
    "-reconnect_delay_max",
    "5",
]


@dataclass
class Track:
    """A single playable YouTube item.

    Attributes:
        url: The canonical source URL (video/playlist item).
        title: The display title.
        stream_url: The direct audio stream URL, if known up front.
        duration: Duration in seconds, if known.
    """

    url: str
    title: str
    stream_url: str | None = None
    duration: int | None = None


class MusicPlayer:
    """Plays a queue of :class:`Track` items via FFmpeg.

    Args:
        ffmpeg: FFmpeg executable path/name.
        on_track_start: Optional callback invoked (from the voice thread)
            whenever a track actually starts playing.
    """

    def __init__(
        self,
        ffmpeg: str,
        *,
        on_track_start: Callable[[Track], None] | None = None,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._queue: deque[Track] = deque()
        self._current: FFmpegPcmSource | None = None
        self._current_track: Track | None = None
        self._loop = False
        self._on_track_start = on_track_start

    @property
    def on_track_start(self) -> Callable[[Track], None] | None:
        """Callback invoked when a track actually starts playing."""
        return self._on_track_start

    @on_track_start.setter
    def on_track_start(self, callback: Callable[[Track], None] | None) -> None:
        """Replace the track-start callback (safe to call from the async layer)."""
        self._on_track_start = callback

    # ------------------------------------------------------------------
    # Queue controls (called from the async layer)
    # ------------------------------------------------------------------

    def enqueue(self, track: Track) -> None:
        """Add a track to the end of the queue."""
        self._queue.append(track)
        log.debug("queued track %r", track.title)

    def set_loop(self, loop: bool) -> None:
        """Enable/disable repeating the current track."""
        self._loop = loop

    def skip(self) -> None:
        """Drop the current track and start the next queued one."""
        self._close_current()
        self._current_track = None
        self._start_next()

    def clear(self) -> None:
        """Drop queued tracks, leaving the current track untouched."""
        self._queue.clear()

    @property
    def queue_size(self) -> int:
        """Number of tracks still queued (excluding the current one)."""
        return len(self._queue)

    @property
    def current_title(self) -> str | None:
        """Title of the currently playing track, if any."""
        return self._current_track.title if self._current_track else None

    @property
    def looping(self) -> bool:
        """Whether the current track loops."""
        return self._loop

    def is_playing(self) -> bool:
        """Whether a track is currently being decoded."""
        return self._current is not None

    # ------------------------------------------------------------------
    # PCM frame consumption (called from the voice thread)
    # ------------------------------------------------------------------

    def read_frame(self) -> bytes | None:
        """Return the next PCM frame, auto-advancing between tracks."""
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _start_next(self) -> None:
        if self._loop and self._current_track is not None:
            track = self._current_track
        elif self._queue:
            track = self._queue.popleft()
        else:
            return
        self._current_track = track
        try:
            self._current = self._open_source(track)
            log.info("youtube started: %s", track.title)
            self._notify_track_started(track)
        except Exception as exc:  # invalid/deleted stream
            log.error("failed to open track %r: %s", track.title, exc, exc_info=exc)
            self._current = None
            self._current_track = None
            if not self._loop:
                self._start_next()

    def _notify_track_started(self, track: Track) -> None:
        """Invoke the track-start callback, if set.

        Called from the voice playback thread, so callers must schedule any
        async work (e.g. sending a Discord message) onto the event loop.
        """
        if self._on_track_start is None:
            return
        try:
            self._on_track_start(track)
        except Exception as exc:  # pragma: no cover - defensive
            log.error("on_track_start callback failed for %r: %s", track.title, exc, exc_info=exc)

    def _open_source(self, track: Track) -> FFmpegPcmSource:
        return FFmpegPcmSource(
            self._ffmpeg,
            input_args=NETWORK_INPUT_ARGS,
            input_path=track.stream_url or track.url,
        )

    def _close_current(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None

    def close(self) -> None:
        """Release the current source and drop the queue."""
        self._close_current()
        self._queue.clear()
        self._current_track = None
