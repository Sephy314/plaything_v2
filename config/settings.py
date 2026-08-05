"""Application settings.

Settings are loaded once and shared across the whole application as a
singleton-like object. Environment variables are validated on creation so
startup failures are reported clearly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import ConfigurationException

BOT_PREFIX = "!"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Validated environment-driven application configuration."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_dsn: str = Field(..., description="PostgreSQL connection string")
    discord_token: str = Field(..., description="Discord bot token")
    meal_url: str = Field("", description="Meal API URL (unused in this stage)")
    mc_parent_directory: str = Field("", description="Minecraft server parent directory")
    log_channel_id: int = Field(0, description="Discord channel id for log output")
    mc_java_command: str = Field("java", description="Java executable used to run servers")
    mc_server_version: str = Field("1.21.4", description="Vanilla server version to download")
    mc_max_memory: str = Field("1G", description="Maximum JVM heap for servers (e.g. 2G)")
    mc_rcon_host: str = Field("127.0.0.1", description="Host the RCON protocol listens on")
    mc_rcon_port: int = Field(25575, description="Default RCON port")
    mc_rcon_password_secret: str = Field(
        "change-me", description="Secret used to derive per-server RCON passwords"
    )
    mc_monitor_interval_seconds: int = Field(30, description="Player-check interval")
    mc_idle_shutdown_seconds: int = Field(
        300, description="Seconds at 0 players before auto shutdown"
    )

    @field_validator("database_dsn")
    @classmethod
    def _validate_database_dsn(cls, value: str) -> str:
        if not value.startswith("postgres"):
            raise ConfigurationException(f"Invalid DATABASE_DSN scheme: {value!r}")
        return value

    @field_validator("discord_token")
    @classmethod
    def _validate_discord_token(cls, value: str) -> str:
        if not value.strip():
            raise ConfigurationException("DISCORD_TOKEN must not be empty")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings: Settings = get_settings()
