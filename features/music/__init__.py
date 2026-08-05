"""Music feature — YouTube video and playlist playback.

Provides prefix commands for playing YouTube audio in Discord voice channels.
Uses the shared AudioManager to allow music and TTS to play simultaneously
without blocking each other.

Supported URLs:
- YouTube video: https://youtube.com/watch?v=xxxxx
- YouTube playlist: https://youtube.com/playlist?list=xxxxx

Commands:
- !재생해 <URL> [계속]  - Play a video or playlist (계속 enables loop)
- !스킵              - Skip to the next track
- !나가              - Stop and leave the voice channel
- !재생정보           - Show current playback status

Features:
- Guild-level queue isolation (each server has its own queue)
- Loop mode for continuous playback
- Automatic track advancement on completion
- Comprehensive error handling and logging
- TTS compatibility (music and speech can play together)
"""
