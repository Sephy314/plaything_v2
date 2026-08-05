"""Shared audio playback layer.

* :mod:`voice.audio.pcm` — PCM frame source abstraction + FFmpeg source.
* :mod:`voice.audio.mixer` — PCM mixing so TTS can play over music.
* :mod:`voice.audio.source` — Discord ``AudioSource`` bridge.
* :mod:`voice.audio.manager` — the shared :class:`AudioManager` facade.
"""

from __future__ import annotations

from voice.audio.manager import AudioManager
from voice.audio.mixer import AudioMixer, mix_pcm
from voice.audio.source import MixedAudioSource

__all__ = ["AudioManager", "AudioMixer", "MixedAudioSource", "mix_pcm"]
