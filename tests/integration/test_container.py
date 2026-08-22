"""Integration test: the composition root wires every service."""

from __future__ import annotations

from config.settings import Settings
from core.container import Container


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_dsn="postgresql://user:pass@localhost/db",
        discord_token="token",
        log_channel_id=123,
    )


def test_container_wires_services() -> None:
    container = Container(_settings())

    assert container.database is not None
    assert container.scheduler is not None
    assert container.task_manager is not None
    assert container.minecraft_service is not None
    assert container.minecraft_backup_service is not None
    assert container.minecraft_backup_scheduler is not None
    assert container.meal_service is not None
    assert container.system_service is not None
    assert container.voice_manager is None  # bound later in bind_bot


def test_container_bind_bot_attaches_voice_manager() -> None:
    container = Container(_settings())

    class FakeBot:
        def event(self, func):
            return func

    container.bind_bot(FakeBot())

    assert container.bot is not None
    assert container.voice_manager is not None
