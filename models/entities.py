"""Domain models: SQLAlchemy-style declarative ORM entities.

These are used by the migration tooling (Alembic) and as runtime value
objects. Runtime data-flow uses ``asyncpg.Record`` and lightweight
dataclasses kept inside each feature's repository.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM entities."""


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    """A Discord user."""

    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class VoiceSetting(Base):
    """Per-user voice configuration."""

    __tablename__ = "voice_settings"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    voice_id: Mapped[str] = mapped_column(String(255), nullable=False)


class MinecraftServer(Base, TimestampMixin):
    """A managed Minecraft server."""

    __tablename__ = "minecraft_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    folder_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="stopped")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MinecraftUser(Base):
    """Links a Discord user to a Minecraft account."""

    __tablename__ = "minecraft_users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    minecraft_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MinecraftSession(Base):
    """Tracks the last known player count for a server."""

    __tablename__ = "minecraft_sessions"

    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("minecraft_servers.id", ondelete="CASCADE"), primary_key=True
    )
    player_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
