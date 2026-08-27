"""Meal feature service layer.

Fetches the daily school meal from the NEIS open API and posts it to a
Discord channel. The meal is only ever triggered by the scheduler — there are
no user-facing meal commands.

The HTTP transport, response parser and target channel are injectable so the
service can be tested without real network or Discord access.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from core.logger import get_logger
from features.meal.parser import MealParseError, MealResult, parse_meal_response

log = get_logger(__name__)

#: Default HTTP timeout for meal API requests (seconds).
DEFAULT_TIMEOUT = 10.0
#: Hard cap for posting the message to Discord (discord.py does not set its
#: own HTTP timeout, so a stalled REST request would otherwise hang forever).
DEFAULT_SEND_TIMEOUT = 30.0
#: Emojis applied to menu lines for a friendly output.
MENU_EMOJIS = ["🍚", "🍲", "🍖", "🍎"]
#: Timezone used to compute the "today" date.
MEAL_TIMEZONE = "Asia/Seoul"


class MealService:
    """Fetch and publish daily meal information.

    Args:
        meal_url: Full NEIS ``mealServiceDietInfo`` URL (``MEAL_URL``).
        http_get: Optional async callable ``(url) -> (status, text)`` used for
            tests; defaults to an aiohttp-based GET.
        parser: Optional response parser; defaults to :func:`parse_meal_response`.
        channel_provider: Optional zero-arg callable returning the target
            channel (or ``None``) at send time.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        meal_url: str,
        *,
        http_get: Callable[[str], Any] | None = None,
        parser: Callable[[dict[str, Any]], MealResult | None] = parse_meal_response,
        channel_provider: Callable[[], Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        send_timeout: float = DEFAULT_SEND_TIMEOUT,
    ) -> None:
        self._meal_url = meal_url
        self._http_get = http_get or self._default_get
        self._parser = parser
        self._channel_provider = channel_provider
        self._timeout = timeout
        self._send_timeout = send_timeout
        self._session: aiohttp.ClientSession | None = None
        #: Channel injected directly via :meth:`set_channel` (bypasses the
        #: ``channel_provider`` cache lookup at send time).
        self._channel: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_meal(self, today: str | None = None) -> MealResult | None:
        """Fetch and parse the meal for the given date.

        Args:
            today: Meal date as ``YYYYMMDD``. Defaults to today (tomorrow if it
                is already past 18:00 in Asia/Seoul).

        Returns:
            A :class:`MealResult` when a meal exists, else ``None`` (no meal).

        Raises:
            MealParseError: If the response cannot be parsed.
            aiohttp.ClientError / TimeoutError: If the request fails.
        """
        if not self._meal_url:
            raise MealParseError("MEAL_URL is not configured")
        date_str = today or self.default_date()
        url = self._build_url(date_str)

        status, text = await self._http_get(url)
        if status != 200:
            raise MealParseError(f"meal API returned HTTP {status}")

        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise MealParseError("meal API returned invalid JSON") from exc

        return self._parser(payload)

    def set_channel(self, channel: Any) -> None:
        """Directly inject the target channel for meal output.

        The daily cron fires at 07:00, when the bot's channel cache may not
        hold the target channel. The resolved channel is injected here and
        used directly at send time instead of relying on a cache lookup
        (``bot.get_channel``) that can return ``None``.
        """
        self._channel = channel

    async def send_meal(self, today: str | None = None) -> MealResult | None:
        """Fetch today's meal and post it to the configured channel.

        Args:
            today: Meal date as ``YYYYMMDD`` (see :meth:`fetch_meal`).

        Returns:
            The published :class:`MealResult`, or ``None`` when there is no
            meal (in which case nothing is sent).
        """
        result = await self.fetch_meal(today)
        if result is None:
            log.info("no meal for date %s — skipping output", today or self.default_date())
            return None

        # Prefer the directly-injected channel; fall back to the provider.
        channel = self._channel
        if channel is None and self._channel_provider is not None:
            channel = self._channel_provider()
        if channel is None:
            # A fetched meal silently dropped is hard to diagnose — make it visible.
            log.warning(
                "meal found for date %s but no target channel is available "
                "(check MEAL_CHANNEL_ID / LOG_CHANNEL_ID) — meal not posted",
                result.date or today or self.default_date(),
            )
            return result

        try:
            await asyncio.wait_for(
                channel.send(self.format_message(result)), timeout=self._send_timeout
            )
        except TimeoutError:
            log.error(
                "failed to send meal message within %ss (Discord request stalled)",
                self._send_timeout,
            )
            raise
        except Exception:
            log.exception("failed to send meal message")
            raise
        log.info(
            "meal published to %s (id=%s) for date %s",
            getattr(channel, "name", "?"),
            getattr(channel, "id", "?"),
            result.date or today or self.default_date(),
        )
        return result

    @staticmethod
    def default_date() -> str:
        """Return today's date in ``YYYYMMDD`` (tomorrow after 18:00 KST)."""
        now = datetime.now(ZoneInfo(MEAL_TIMEZONE))
        if now.hour >= 18:
            now = now + timedelta(days=1)
        return now.strftime("%Y%m%d")

    async def close(self) -> None:
        """Close the underlying HTTP session, if any."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_url(self, date_str: str) -> str:
        separator = "&" if "?" in self._meal_url else "?"
        return f"{self._meal_url}{separator}MLSV_YMD={date_str}"

    @staticmethod
    def format_message(result: MealResult) -> str:
        """Render a :class:`MealResult` as a Discord-friendly message."""
        lines = result.menu.splitlines()
        menu_lines = "\n".join(
            f"{MENU_EMOJIS[index % len(MENU_EMOJIS)]} {line}" for index, line in enumerate(lines)
        )
        parts = [f"📅 **오늘의 급식 ({result.date})**", "", "🍱 **메뉴:**", menu_lines]
        if result.calories:
            parts.extend(["", f"🔥 **칼로리:** {result.calories}"])
        return "\n".join(parts)

    async def _default_get(self, url: str) -> tuple[int, str]:
        session = await self._session_or_create()
        async with session.get(url) as response:
            text = await response.text()
            return response.status, text

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session
