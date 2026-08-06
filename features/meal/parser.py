"""NEIS meal API response parser.

Converts the raw JSON returned by the ``mealServiceDietInfo`` endpoint into a
:class:`MealResult`. The parser is a pure function so it can be unit-tested
with canned payloads and reused by any HTTP transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: NEIS result code returned when there is no meal for the requested date.
RESULT_NO_MEAL = "INFO-200"
#: NEIS result code returned on success.
RESULT_SUCCESS = "INFO-000"


class MealParseError(Exception):
    """Raised when the meal API response cannot be understood."""


@dataclass(frozen=True)
class MealResult:
    """Parsed meal data for a single day.

    Attributes:
        date: Meal date in ``YYYYMMDD`` form.
        menu: Multi-line menu (one dish per line).
        calories: Calorie string, e.g. ``"612.4 Kcal"`` (may be empty).
    """

    date: str
    menu: str
    calories: str = ""


def _extract_result_code(data: dict[str, Any]) -> str | None:
    """Return the NEIS ``RESULT.CODE`` from a response payload."""
    try:
        return data["mealServiceDietInfo"][0]["head"][1]["RESULT"]["CODE"]
    except (KeyError, IndexError, TypeError):
        result = data.get("RESULT")
        if isinstance(result, dict):
            return result.get("CODE")
        return None


def _clean_menu(raw: str) -> str:
    """Normalize a ``DDISH_NM`` string into a clean multi-line menu."""
    menu = raw.replace("<br/>", "\n").replace("<BR/>", "\n")
    lines = [line.strip() for line in menu.splitlines() if line.strip()]
    return "\n".join(lines)


def parse_meal_response(data: dict[str, Any]) -> MealResult | None:
    """Parse a NEIS ``mealServiceDietInfo`` JSON payload.

    Args:
        data: Decoded JSON response body.

    Returns:
        A :class:`MealResult` when a meal exists, or ``None`` when there is no
        meal for the requested date (``INFO-200``).

    Raises:
        MealParseError: When the response is malformed or returns an unknown
            result code.
    """
    code = _extract_result_code(data)

    if code == RESULT_NO_MEAL:
        return None
    if code != RESULT_SUCCESS:
        if code is None:
            raise MealParseError("meal response missing RESULT code")
        raise MealParseError(f"unexpected meal result code: {code}")

    try:
        rows = data["mealServiceDietInfo"][1]["row"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MealParseError("meal response missing row data") from exc

    if not rows:
        return None

    meal = rows[0]
    return MealResult(
        date=str(meal.get("MLSV_YMD", "")),
        menu=_clean_menu(meal.get("DDISH_NM", "")),
        calories=str(meal.get("CAL_INFO", "") or ""),
    )
