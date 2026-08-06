"""Composition root and dependency injection container.

Constructs the service graph once and makes shared dependencies available
to cogs and startup code without scattering globals.
"""

from __future__ import annotations

from typing import Any

from config.settings import Settings
from core.database import Database
from core.logger import get_logger
from core.scheduler import Scheduler
from features.meal.scheduler import MealScheduler
from features.meal.service import MealService
from repository.minecraft_repository import MinecraftRepository
from repository.tts_voice_repository import TTSVoiceRepository
from repository.user_repository import UserRepository
from repository.voice_repository import VoiceRepository
from services.minecraft_service import MinecraftService
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

        self.user_repository = UserRepository(self.database)
        self.voice_repository = VoiceRepository(self.database)
        self.minecraft_repository = MinecraftRepository(self.database)
        self.tts_voice_repository = TTSVoiceRepository(self.database)

        self.user_service = UserService(self.database)
        self.voice_service = VoiceService(self.voice_repository)
        self.minecraft_service = MinecraftService(self.minecraft_repository, settings)
        self.tts_voice_service = TTSVoiceService(self.tts_voice_repository)

        self.meal_service = MealService(settings.meal_url)
        self.meal_scheduler = MealScheduler(
            self.scheduler,
            self.meal_service,
            timezone=settings.timezone,
        )

        self.youtube = YoutubeClient()
        self.tts_providers = ProviderRegistry()

        self.voice_manager: VoiceManager | None = None
        self.log_queue: Any | None = None
        self.bot = None

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
        if self.bot is None:
            return None
        channel_id = self.settings.meal_channel_id or self.settings.log_channel_id
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id)

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
