"""Composition root and dependency injection container.

Constructs the service graph once and makes shared dependencies available
to cogs and startup code without scattering globals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from config.settings import Settings
from core.database import Database
from core.logger import get_logger
from core.scheduler import Scheduler
from core.task_manager import TaskManager
from features.meal.scheduler import MealScheduler
from features.meal.service import MealService
from features.minecraft.scheduler import MinecraftBackupScheduler
from repository.minecraft_repository import MinecraftRepository
from repository.tts_voice_repository import TTSVoiceRepository
from repository.user_repository import UserRepository
from repository.voice_repository import VoiceRepository
from services.minecraft_backup_service import MinecraftBackupService
from services.minecraft_service import MinecraftService
from services.system_service import SystemService
from services.tts_voice_service import TTSVoiceService
from services.user_service import UserService
from services.voice_service import VoiceService
from voice.audio.manager import AudioManager
from voice.manager import VoiceManager
from voice.music.youtube import YoutubeClient
from voice.tts.provider import ProviderRegistry

log = get_logger(__name__)


class Container:
    """Holds initialized shared components."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_dsn)
        self.scheduler = Scheduler(timezone=settings.timezone)
        self.task_manager = TaskManager()
        self.started_at = datetime.now(UTC)
        #: Process exit code requested by an admin lifecycle command, or None.
        self.shutdown_exit_code: int | None = None

        self.user_repository = UserRepository(self.database)
        self.voice_repository = VoiceRepository(self.database)
        self.minecraft_repository = MinecraftRepository(self.database)
        self.tts_voice_repository = TTSVoiceRepository(self.database)

        self.user_service = UserService(self.database)
        self.voice_service = VoiceService(self.voice_repository)
        self.minecraft_service = MinecraftService(self.minecraft_repository, settings)
        self.minecraft_backup_service = MinecraftBackupService(self.minecraft_service, settings)
        self.minecraft_backup_scheduler = MinecraftBackupScheduler(
            self.scheduler,
            self.minecraft_backup_service,
            timezone=settings.timezone,
            hour=settings.mc_backup_hour,
            minute=settings.mc_backup_minute,
        )
        self.tts_voice_service = TTSVoiceService(self.tts_voice_repository)

        self.meal_service = MealService(settings.meal_url)
        self.meal_scheduler = MealScheduler(
            self.scheduler,
            self.meal_service,
            timezone=settings.timezone,
        )

        self.system_service = SystemService(self)

        self.youtube = YoutubeClient()
        self.tts_providers = ProviderRegistry()

        self.voice_manager: VoiceManager | None = None
        self.log_queue: Any | None = None
        self.bot = None
        #: Cached meal target channel resolved at startup (see resolve_meal_channel).
        self._meal_channel = None

    def bind_bot(self, bot) -> None:
        """Attach the Discord bot and voice manager after bot creation.

        Args:
            bot: The created :class:`commands.Bot`.
        """
        self.bot = bot
        self.voice_manager = VoiceManager(
            bot,
            ffmpeg=self.settings.ffmpeg_executable,
            audio_factory=self._build_audio,
        )
        self.meal_service.channel_provider = self._meal_channel_provider

    def _meal_channel_provider(self):
        """Return the channel for meal output, or ``None`` if unavailable."""
        channel_id = self.settings.meal_channel_id or self.settings.log_channel_id
        if not channel_id:
            return None
        if self._meal_channel is not None and self._meal_channel.id == channel_id:
            return self._meal_channel
        if self.bot is None:
            return None
        return self.bot.get_channel(channel_id)

    async def resolve_meal_channel(self) -> None:
        """Resolve the meal target channel and inject it into the meal service.

        Tries the bot's channel cache first, then the REST API, so a valid
        channel is found even when it is not cached. The resolved channel is
        injected directly into :class:`MealService` so the daily cron uses it
        even if the bot's cache misses at 07:00. Logs the resolved channel
        (INFO, with its id) or an ERROR when it cannot be resolved — the first
        thing to check when the meal is not being posted.
        """
        channel_id = self.settings.meal_channel_id or self.settings.log_channel_id
        if not channel_id:
            log.error(
                "meal channel not configured: MEAL_CHANNEL_ID and LOG_CHANNEL_ID are both unset"
            )
            self.meal_service.set_channel(None)
            return
        channel = self.bot.get_channel(channel_id) if self.bot is not None else None
        if channel is None and self.bot is not None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as exc:
                log.error("meal channel %s could not be fetched via REST: %s", channel_id, exc)
                self.meal_service.set_channel(None)
                return
        if channel is None:
            log.error(
                "meal channel %s could not be resolved — meal will not be posted "
                "(check MEAL_CHANNEL_ID / LOG_CHANNEL_ID)",
                channel_id,
            )
            self.meal_service.set_channel(None)
            return
        self._meal_channel = channel
        self.meal_service.set_channel(channel)
        log.info(
            "meal channel resolved: %s (id=%s)",
            getattr(channel, "name", "?"),
            channel.id,
        )

    def _build_audio(self, voice_client: Any) -> AudioManager:
        """Construct an :class:`AudioManager` bound to a voice client.

        Args:
            voice_client: The Discord voice client for a guild.

        Returns:
            A configured :class:`AudioManager`.
        """
        return AudioManager(
            voice_client,
            ffmpeg=self.settings.ffmpeg_executable,
            provider_registry=self.tts_providers,
            tts_voice_service=self.tts_voice_service,
            youtube=self.youtube,
        )


container: Container | None = None


def init_container(settings: Settings) -> Container:
    """Initialise the process-wide container.

    Args:
        settings: Validated application settings.

    Returns:
        The configured :class:`Container`.
    """
    global container
    container = Container(settings)
    log.info("dependency container initialized")
    return container


def get_container() -> Container:
    """Return the initialised container.

    Raises:
        RuntimeError: If the container was not initialised.
    """
    if container is None:
        raise RuntimeError("Container not initialised — call init_container first")
    return container
