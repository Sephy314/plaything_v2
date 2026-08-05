"""Abstract audio playback interface.

Both the future Youtube feature and the TTS feature will implement this
interface so callers can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AudioManager(ABC):
    """Common contract for audio playback backends.

    Subclasses manage a queue of playable sources and expose queue controls.
    """

    @abstractmethod
    async def enqueue(self, source: Any) -> None:
        """Add a playable source to the queue.

        Args:
            source: Backend-specific playable source.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop current playback and discard the queue."""

    @abstractmethod
    async def skip(self) -> None:
        """Skip the currently playing item and continue with the queue."""

    @abstractmethod
    async def clear(self) -> None:
        """Clear the queue without interrupting current playback."""
