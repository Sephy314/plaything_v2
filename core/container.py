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
from repository.minecraft_repository import MinecraftRepository
from repository.user_repository import UserRepository
from repository.voice_repository import VoiceRepository
from services.minecraft_service import MinecraftService
from services.user_service import UserService
from services.voice_service import VoiceService
from voice.manager import VoiceManager

log = get_logger(__name__)


class Container:
    """Holds initialized shared components."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_dsn)
        self.scheduler = Scheduler()

        self.user_repository = UserRepository(self.database)
        self.voice_repository = VoiceRepository(self.database)
        self.minecraft_repository = MinecraftRepository(self.database)

        self.user_service = UserService(self.database)
        self.voice_service = VoiceService(self.voice_repository)
        self.minecraft_service = MinecraftService(self.minecraft_repository, settings)

        self.voice_manager: VoiceManager | None = None
        self.log_queue: Any | None = None
        self.bot = None

    def bind_bot(self, bot) -> None:
        """Attach the Discord bot and voice manager after bot creation.

        Args:
            bot: The created :class:`commands.Bot`.
        """
        self.bot = bot
        self.voice_manager = VoiceManager(bot)


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
