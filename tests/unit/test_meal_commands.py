"""Tests for the meal debug/integration-test commands.

The cog commands are thin wrappers over ``MealService``; here we drive them
with fake interactions and injected services to cover the success, no-meal,
error and invalid-date paths.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from features.meal.commands import MealCog
from features.meal.service import MealService


def _success_payload() -> dict:
    return {
        "mealServiceDietInfo": [
            {
                "head": [
                    {"list_total_count": 1},
                    {"RESULT": {"CODE": "INFO-000", "MESSAGE": "성공"}},
                ]
            },
            {
                "row": [
                    {
                        "MLSV_YMD": "20260806",
                        "DDISH_NM": "쌀밥<br/>된장국",
                        "CAL_INFO": "612.4 Kcal",
                    }
                ]
            },
        ]
    }


def _no_meal_payload() -> dict:
    return {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당 데이터가 없습니다."}}


class _FakeBot:
    pass


class _FakeResponse:
    async def defer(self) -> None:
        return None


class _FakeInteraction:
    """Minimal stand-in for a Discord interaction."""

    def __init__(self) -> None:
        self.response = _FakeResponse()
        self.followup = SimpleNamespace(send=AsyncMock())


def _make_cog(service) -> MealCog:
    cog = MealCog(_FakeBot())
    cog._service = service  # bypass container lookup
    return cog


@pytest.mark.asyncio
async def test_meal_command_publishes_today() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_success_payload())

    service = MealService("http://meal.test/api", http_get=fake_get, channel_provider=lambda: None)
    cog = _make_cog(service)

    interaction = _FakeInteraction()
    # `@app_commands.command` stores the unbound handler in `.callback`;
    # bind it to the cog by passing `cog` explicitly as `self`.
    await cog.meal.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    content = interaction.followup.send.await_args.args[0]
    assert "오늘의 급식" in content
    assert "쌀밥" in content


@pytest.mark.asyncio
async def test_meal_command_no_meal() -> None:
    async def fake_get(url: str) -> tuple[int, str]:
        return 200, json.dumps(_no_meal_payload())

    service = MealService("http://meal.test/api", http_get=fake_get, channel_provider=lambda: None)
    cog = _make_cog(service)

    interaction = _FakeInteraction()
    await cog.meal.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    content = interaction.followup.send.await_args.args[0]
    assert "급식이 없습니다" in content


@pytest.mark.asyncio
async def test_meal_command_error_reply() -> None:
    class FailingService:
        async def send_meal(self, today: str | None = None) -> None:
            raise RuntimeError("boom")

    cog = _make_cog(FailingService())

    interaction = _FakeInteraction()
    await cog.meal.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    content = interaction.followup.send.await_args.args[0]
    assert "급식 조회에 실패했습니다" in content
    assert "boom" in content


@pytest.mark.asyncio
async def test_meal_date_command_invalid_date() -> None:
    class FakeService:
        async def send_meal(self, today: str | None = None) -> None:
            raise AssertionError("send_meal must not be called")

    cog = _make_cog(FakeService())

    interaction = _FakeInteraction()
    await cog.meal_date.callback(cog, interaction, year=2026, month=8)  # missing day

    interaction.followup.send.assert_awaited_once()
    content = interaction.followup.send.await_args.args[0]
    assert "날짜를 올바르게 입력" in content


def test_compose_date() -> None:
    assert MealCog._compose_date(2026, 8, 6) == "20260806"
    assert MealCog._compose_date(None, None, None) is None
    assert MealCog._compose_date(2026, None, None) is None
    assert MealCog._compose_date(2026, 13, 1) is None
    assert MealCog._compose_date(2026, 8, 32) is None
