"""Application settings.

Settings are loaded once and shared across the whole application as a
singleton-like object. Environment variables are validated on creation so
startup failures are reported clearly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
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
    meal_url: str = Field("", description="NEIS meal API URL (mealServiceDietInfo base)")
    meal_channel_id: int = Field(
        0,
        description=(
            "Discord channel id for the daily meal output "
            "(0 = fall back to LOG_CHANNEL_ID)"
        ),
    )
    timezone: str = Field(
        "Asia/Seoul",
        description="Primary timezone used by the scheduler and log timestamps",
    )
    mc_parent_directory: str = Field("", description="Minecraft server parent directory")
    mc_port_start: int = Field(25565, description="First Minecraft port (published range)")
    mc_port_end: int = Field(25620, description="Last Minecraft port (published range)")
    mc_public_host: str = Field(
        "",
        validation_alias=AliasChoices("MC_PUBLIC_HOST", "MC_EXTERNAL_HOST"),
        description="Public IP/hostname players use to connect (e.g. 1.2.3.4)",
    )
    mc_internal_host: str = Field(
        "", description="Internal address (same machine) for players on the host"
    )
    log_channel_id: int = Field(0, description="Discord channel id for log output")
    mc_java_command: str = Field("java", description="Java executable used to run servers")
    mc_server_flavor: str = Field(
        "paper", description="Server jar flavor: 'paper' (default) or 'vanilla'"
    )
    mc_server_version: str = Field("1.21.4", description="Server version to download")
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
    mc_backup_directory: str = Field(
        "./backups",
        description="Root directory that holds per-server world backups",
    )
    mc_backup_retention_days: int = Field(
        90,
        description="Delete backups older than this many days (kept while no newer backup exists)",
    )
    ffmpeg_executable: str = Field("ffmpeg", description="FFmpeg executable for audio playback")

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
