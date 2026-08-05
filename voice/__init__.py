"""Voice playback infrastructure.

* ``voice/manager.py`` — guild-level :class:`VoiceManager`.
* ``voice/audio`` — shared mixing / source / audio manager layer.
* ``voice/tts`` — TTS providers and player.
* ``voice/music`` — YouTube extraction and music player.
"""

from __future__ import annotations

from voice.audio.manager import AudioManager
from voice.manager import VoiceManager

__all__ = ["AudioManager", "VoiceManager"]
