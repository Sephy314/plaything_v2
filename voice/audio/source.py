"""Discord audio source that streams mixed PCM from an :class:`AudioMixer`.

Returning raw PCM (``is_opus() == False``) makes discord.py encode each
frame via its internal Opus encoder, allowing us to mix music and TTS into a
single stream that plays on one voice client.
"""

from __future__ import annotations

from discord import AudioSource

from voice.audio.mixer import AudioMixer


class MixedAudioSource(AudioSource):
    """Bridges the mixer to the Discord voice send loop.

    Args:
        mixer: The shared :class:`AudioMixer` producing mixed PCM frames.
    """

    def __init__(self, mixer: AudioMixer) -> None:
        self._mixer = mixer

    def read(self) -> bytes:
        """Return the next mixed PCM frame."""
        return self._mixer.next_frame()

    def is_opus(self) -> bool:
        """This source provides raw PCM, not Opus."""
        return False

    def cleanup(self) -> None:
        """Release mixer resources when playback ends."""
        self._mixer.close()
