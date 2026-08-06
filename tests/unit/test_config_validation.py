"""Tests for startup configuration validation (bot/main.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.main import validate_config
from core.exceptions import ConfigurationException


def test_validate_config_passes_when_log_channel_configured() -> None:
    settings = SimpleNamespace(log_channel_id=123456789)

    validate_config(settings)  # should not raise


def test_validate_config_rejects_missing_log_channel() -> None:
    settings = SimpleNamespace(log_channel_id=0)

    with pytest.raises(ConfigurationException, match="LOG_CHANNEL_ID"):
        validate_config(settings)
