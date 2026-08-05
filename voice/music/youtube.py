"""YouTube extraction client built on yt-dlp.

Resolves a URL (single video or playlist) into a list of playable
:class:`Track` objects with direct audio stream URLs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.exceptions import YoutubeError
from core.logger import get_logger
from voice.music.player import Track

log = get_logger(__name__)

_DEFAULT_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "extract_flat": False,
}


class YoutubeClient:
    """Thin wrapper around yt-dlp exposing async extraction."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self._options = {**_DEFAULT_OPTIONS, **(options or {})}

    async def extract(self, url: str) -> list[Track]:
        """Resolve a URL into a list of playable tracks.

        Args:
            url: A YouTube video or playlist URL.

        Returns:
            A non-empty list of :class:`Track` items.

        Raises:
            YoutubeError: If the URL is invalid or nothing is playable.
        """
        if not url or not url.strip():
            raise YoutubeError("Empty YouTube URL")
        loop = asyncio.get_running_loop()
        try:
            tracks = await loop.run_in_executor(None, self._extract_sync, url.strip())
        except YoutubeError:
            raise
        except Exception as exc:
            raise YoutubeError(f"Failed to resolve YouTube URL: {exc}") from exc
        if not tracks:
            raise YoutubeError("No playable audio found for the given URL")
        return tracks

    def _extract_sync(self, url: str) -> list[Track]:
        import yt_dlp  # type: ignore[import-not-found]  # noqa: PLC0415

        try:
            with yt_dlp.YoutubeDL(self._options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise YoutubeError(f"yt-dlp could not process the URL: {exc}") from exc

        if info is None:
            raise YoutubeError("yt-dlp returned no data")

        entries = info.get("entries")
        if entries is not None:
            tracks = [track for entry in entries if (track := self._to_track(entry))]
            if not tracks:
                raise YoutubeError("No playable entries found in the playlist")
            return tracks
        track = self._to_track(info)
        if track is None:
            raise YoutubeError("Unable to build a playable track")
        return [track]

    @staticmethod
    def _to_track(info: dict[str, Any] | None) -> Track | None:
        if not info:
            return None
        stream_url = info.get("url")
        if not stream_url and info.get("formats"):
            stream_url = info["formats"][0].get("url")
        if not stream_url:
            log.warning("no stream url resolved for %r", info.get("id"))
            return None
        return Track(
            url=info.get("webpage_url") or info.get("original_url") or str(info.get("id", "")),
            title=info.get("title") or "Unknown track",
            stream_url=stream_url,
            duration=info.get("duration"),
        )
