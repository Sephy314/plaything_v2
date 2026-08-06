"""Tests for the meal feature (parser + service).

Covers: normal fetch, no-meal, API failure, parser failure, and Discord
publish/skip behaviour using fully mocked HTTP/channel transports.
"""

from __future__ import annotations

import json

import aiohttp
import pytest

from features.meal.parser import MealParseError, parse_meal_response
from features.meal.service import MealService


def _success_payload(
    date: str = "20260806",
    menu: str = "쌀밥<br/>된장국<br/>불고기<br/>사과",
    cal: str = "612.4 Kcal",
) -> dict:
    return {
        "mealServiceDietInfo": [
            {
                "head": [
                    {"list_total_count": 1},
                    {"RESULT": {"CODE": "INFO-000", "MESSAGE": "성공"}},
                ]
            },
            {"row": [{"MLSV_YMD": date, "DDISH_NM": menu, "CAL_INFO": cal}]},
        ]
    }


def _no_meal_payload() -> dict:
    return {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당 데이터가 없습니다."}}


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


def test_parse_meal_success() -> None:
    result = parse_meal_response(_success_payload())
    assert result is not None
    assert result.date == "20260806"
    assert result.menu == "쌀밥\n된장국\n불고기\n사과"
    assert result.calories == "612.4 Kcal"


def test_parse_meal_handles_br_tag() -> None:
    result = parse_meal_response(_success_payload(menu="밥<br/>국<br/>반찬<br/>후식"))
    assert result.menu == "밥\n국\n반찬\n후식"


def test_parse_meal_no_meal_returns_none() -> None:
    assert parse_meal_response(_no_meal_payload()) is None


def test_parse_meal_missing_code_raises() -> None:
    with pytest.raises(MealParseError, match="missing RESULT code"):
        parse_meal_response({})


def test_parse_meal_unknown_code_raises() -> None:
    with pytest.raises(MealParseError, match="unexpected meal result code"):
        parse_meal_response({"RESULT": {"CODE": "ERROR-999"}})


# ----------------------------------------------------------------------
# Service — fetch
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_meal_success() -> None:
    captured: dict[str, str] = {}

    async def fake_get(url: str) -> tuple[int, str]:
        captured["url"] = url
        return 200, json.dumps(_success_payload())

    service = MealService("http://meal.test/api", http_get=fake_get)
    result = await service.fetch_meal("20260806")

    assert result is not None
    assert result.date == "20260806"
    assert "쌀밥" in result.menu
    assert "MLSV_YMD=20260806" in captured["url"]


@pytest.mark.asyncio
async def test_fetch_meal_no_meal() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_no_meal_payload())

    service = MealService("http://meal.test/api", http_get=fake_get)
    assert await service.fetch_meal("20260806") is None


@pytest.mark.asyncio
async def test_fetch_meal_http_error_status() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 500, "oops"

    service = MealService("http://meal.test/api", http_get=fake_get)
    with pytest.raises(MealParseError, match="HTTP 500"):
        await service.fetch_meal("20260806")


@pytest.mark.asyncio
async def test_fetch_meal_invalid_json() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, "not-json"

    service = MealService("http://meal.test/api", http_get=fake_get)
    with pytest.raises(MealParseError, match="invalid JSON"):
        await service.fetch_meal("20260806")


@pytest.mark.asyncio
async def test_fetch_meal_parser_failure_propagates() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps({})

    service = MealService("http://meal.test/api", http_get=fake_get)
    with pytest.raises(MealParseError):
        await service.fetch_meal("20260806")


@pytest.mark.asyncio
async def test_fetch_meal_network_error_propagates() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        raise aiohttp.ClientConnectionError("boom")

    service = MealService("http://meal.test/api", http_get=fake_get)
    with pytest.raises(aiohttp.ClientConnectionError):
        await service.fetch_meal("20260806")


@pytest.mark.asyncio
async def test_fetch_meal_without_url_fails_cleanly() -> None:
    service = MealService("")
    with pytest.raises(MealParseError, match="MEAL_URL is not configured"):
        await service.fetch_meal("20260806")


# ----------------------------------------------------------------------
# Service — send
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_meal_publishes_to_channel() -> None:
    sent: list[str] = []

    class FakeChannel:
        async def send(self, content: str) -> None:
            sent.append(content)

    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_success_payload())

    service = MealService(
        "http://meal.test/api",
        http_get=fake_get,
        channel_provider=lambda: FakeChannel(),
    )
    result = await service.send_meal("20260806")

    assert result is not None
    assert len(sent) == 1
    assert "오늘의 급식" in sent[0]
    assert "쌀밥" in sent[0]


@pytest.mark.asyncio
async def test_send_meal_no_meal_sends_nothing() -> None:
    sent: list[str] = []

    class FakeChannel:
        async def send(self, content: str) -> None:
            sent.append(content)

    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_no_meal_payload())

    service = MealService(
        "http://meal.test/api",
        http_get=fake_get,
        channel_provider=lambda: FakeChannel(),
    )
    result = await service.send_meal("20260806")

    assert result is None
    assert sent == []


@pytest.mark.asyncio
async def test_send_meal_channel_missing_still_fetches() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_success_payload())

    service = MealService(
        "http://meal.test/api",
        http_get=fake_get,
        channel_provider=lambda: None,
    )
    assert await service.send_meal("20260806") is not None


@pytest.mark.asyncio
async def test_send_meal_discord_error_propagates() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_success_payload())

    class BrokenChannel:
        async def send(self, content: str) -> None:
            raise RuntimeError("no permission")

    service = MealService(
        "http://meal.test/api",
        http_get=fake_get,
        channel_provider=lambda: BrokenChannel(),
    )
    with pytest.raises(RuntimeError):
        await service.send_meal("20260806")


# ----------------------------------------------------------------------
# Date selection
# ----------------------------------------------------------------------


def _patch_datetime(monkeypatch: pytest.MonkeyPatch, hour: int) -> None:
    import features.meal.service as meal_module

    class _FakeNow:
        def __init__(self) -> None:
            self.hour = hour

        def __add__(self, other: object) -> _FakeNow:
            return self

        def strftime(self, fmt: str) -> str:
            return "20260807" if self.hour >= 18 else "20260806"

    class _FakeDatetime:
        @staticmethod
        def now(tz=None) -> _FakeNow:
            return _FakeNow()

    monkeypatch.setattr(meal_module, "datetime", _FakeDatetime)


def test_default_date_before_18(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_datetime(monkeypatch, hour=10)
    assert MealService.default_date() == "20260806"


def test_default_date_after_18_is_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_datetime(monkeypatch, hour=19)
    assert MealService.default_date() == "20260807"
