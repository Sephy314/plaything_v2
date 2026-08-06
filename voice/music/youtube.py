"""YouTube extraction client built on yt-dlp.

Resolves a URL (single video or playlist) into a list of playable
:class:`Track` objects with direct audio stream URLs.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from core.exceptions import YoutubeError
from core.logger import get_logger
from voice.music.player import Track

log = get_logger(__name__)

_SEARCH_PREFIX = re.compile(r"^ytsearch\d*:", re.IGNORECASE)

_DEFAULT_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "extract_flat": False,
}

_EXTRACT_ATTEMPTS = 3


class YoutubeClient:
    """Thin wrapper around yt-dlp exposing async extraction."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self._options = {**_DEFAULT_OPTIONS, **(options or {})}

    async def extract(self, url: str) -> list[Track]:
        """Resolve a URL or search query into a list of playable tracks.

        Args:
            url: A YouTube video, playlist URL, or search query.

        Returns:
            A non-empty list of :class:`Track` items.

        Raises:
            YoutubeError: If the URL is invalid or nothing is playable.
        """
        if not url or not url.strip():
            raise YoutubeError("Empty YouTube URL")

        url_stripped = url.strip()
        # Automatically convert search terms to a ytsearch query. Avoid
        # double-prefixing values that already carry a ytsearchN: prefix, such
        # as the autocomplete presets ("ytsearch1:...").
        if not (
            url_stripped.startswith(("http://", "https://"))
            or _SEARCH_PREFIX.match(url_stripped)
        ):
            url_stripped = f"ytsearch1:{url_stripped}"

        loop = asyncio.get_running_loop()
        # YouTube intermittently throttles / bot-walls requests, returning
        # entries without a playable stream URL. Retry with a short backoff so
        # transient failures do not surface to the user as hard errors.
        last_error: YoutubeError | None = None
        for attempt in range(_EXTRACT_ATTEMPTS):
            try:
                tracks = await loop.run_in_executor(None, self._extract_sync, url_stripped)
                if tracks:
                    return tracks
            except YoutubeError as exc:
                last_error = exc
            if attempt + 1 < _EXTRACT_ATTEMPTS:
                await asyncio.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise YoutubeError("No playable audio found for the given URL")

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
            tracks = [track for entry in entries if (track := self._resolve_entry(entry))]
            if not tracks:
                raise YoutubeError("No playable entries found in the playlist")
            return tracks
        track = self._resolve_entry(info)
        if track is None:
            raise YoutubeError("Unable to build a playable track")
        return [track]

    def _resolve_entry(self, info: dict[str, Any] | None) -> Track | None:
        """Build a playable :class:`Track` from an extraction entry.

        A search/playlist entry can come back "flat" (no stream URL) when
        YouTube throttles the request. In that case fall back to re-extracting
        the entry by its video page URL before giving up on it.
        """
        track = self._to_track(info)
        if track is not None:
            return track
        source = (info or {}).get("webpage_url") or (info or {}).get("original_url")
        if not source:
            return None
        try:
            import yt_dlp  # type: ignore[import-not-found]  # noqa: PLC0415

            with yt_dlp.YoutubeDL(self._options) as ydl:
                detail = ydl.extract_info(source, download=False)
            return self._to_track(detail)
        except Exception as exc:
            log.warning("failed to re-extract track %r: %s", source, exc)
            return None

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
