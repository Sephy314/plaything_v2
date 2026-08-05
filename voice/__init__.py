"""Voice playback infrastructure (design stage).

* ``voice/audio_manager.py`` defines the shared ``AudioManager`` interface
  used by future Youtube and TTS features.
* ``voice/manager.py`` provides the ``VoiceManager`` that tracks Discord
  voice connections per guild.
"""

from __future__ import annotations

from voice.audio_manager import AudioManager
from voice.manager import VoiceManager

__all__ = ["AudioManager", "VoiceManager"]
