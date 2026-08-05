"""Application-wide exception hierarchy.

Every domain has a dedicated exception so callers can handle failures
precisely while base classes allow generic catch-all handling.
"""

from __future__ import annotations


class AppException(Exception):
    """Base class for all application-specific exceptions."""


class ConfigurationException(AppException):
    """Raised when configuration / environment is invalid."""


class DatabaseException(AppException):
    """Raised when a database operation fails."""


class DiscordException(AppException):
    """Raised when a Discord-related operation fails."""


class VoiceException(AppException):
    """Raised when a voice operation fails."""


class MusicException(AppException):
    """Base class for music playback failures."""


class YoutubeError(MusicException):
    """Raised when a YouTube URL cannot be resolved or is unavailable."""


class PlaybackError(MusicException):
    """Raised when an audio source cannot be played."""


class ValidationException(AppException):
    """Raised when input validation fails."""


class MinecraftException(AppException):
    """Base class for Minecraft-specific failures."""


class MinecraftServerNotFound(MinecraftException):
    """Raised when a requested server does not exist."""


class MinecraftAliasExists(MinecraftException):
    """Raised when creating a server with a duplicate alias."""


class MinecraftPortConflict(MinecraftException):
    """Raised when the requested port is already in use."""


class MinecraftFolderError(MinecraftException):
    """Raised when server folder setup fails."""


class MinecraftProcessError(MinecraftException):
    """Raised when the server process fails to start or stop."""


class MinecraftRconError(MinecraftException):
    """Raised when an RCON operation fails."""


class MinecraftPermissionError(MinecraftException):
    """Raised when a user lacks the required Minecraft OP permission."""


class MinecraftUnauthorized(MinecraftException):
    """Raised when a Discord admin-only operation is attempted."""
