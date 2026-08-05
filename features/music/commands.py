"""Music feature commands — slash commands `/재생해`, `/스킵`, `/나가`, `/재생정보`.

Provides YouTube video and playlist playback via Discord voice channels.

Supports:
- YouTube video URLs and playlist URLs
- Queue management with automatic advancement
- Loop (repeat) mode
- Skip to next track
- Stop and leave voice channel

All playback is coordinated through the shared AudioManager, which mixes
YouTube tracks with TTS output so both can play simultaneously.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext.commands import Bot

from core.exceptions import VoiceException, YoutubeError
from core.logger import get_logger
from features.base import FeatureCog
from features.music.service import MusicService

log = get_logger(__name__)


# ------------------------------------------------------------------
# Autocomplete callbacks (module-level)
# ------------------------------------------------------------------


def _contains_match(text: str, current: str) -> bool:
    """Check if text matches current input (case-insensitive, partial match)."""
    current_lower = current.lower()
    text_lower = text.lower()
    
    # Exact prefix match
    if text_lower.startswith(current_lower):
        return True
    # Partial word match
    if current_lower in text_lower:
        return True
    return False


async def music_url_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for music URL parameter."""
    presets = [
        ("Lo-Fi Beats (공부/작업용)", "https://www.youtube.com/watch?v=jfKfPfyJRdk"),
        ("Jazz/Chill Café Music (카페 음악)", "https://www.youtube.com/watch?v=tNkZsKeCyLH"),
        ("Synthwave / Retro Synth (신스웨이브)", "https://www.youtube.com/watch?v=4xDzrJKXOOY"),
        ("Piano Study Music (클래식 피아노)", "https://www.youtube.com/watch?v=H17_S6Csz9c"),
        ("Gaming Chill Mix (게임 브금)", "https://www.youtube.com/watch?v=fKOPQ562mhs"),
    ]
    
    # Filter with both prefix and partial matching
    filtered = [
        (name, url) for name, url in presets 
        if not current or _contains_match(name, current) or _contains_match(url, current)
    ]
    
    return [
        app_commands.Choice(name=name, value=url)
        for name, url in filtered[:25]
    ]


class MusicCog(FeatureCog):
    """Slash commands for YouTube music playback."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._service: MusicService | None = None

    @property
    def service(self) -> MusicService:
        """Lazy-load the MusicService from container."""
        if self._service is None:
            from core.container import get_container

            container = get_container()
            self._service = MusicService(
                container.voice_manager,
                container.youtube,
                log_channel_id=container.settings.log_channel_id,
            )
        return self._service

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(name="재생해", description="YouTube 영상 또는 플레이리스트를 재생합니다.")
    @app_commands.describe(
        url="YouTube 영상 또는 플레이리스트 URL",
        loop="반복 재생 여부 (계속)",
    )
    @app_commands.autocomplete(url=music_url_autocomplete)
    async def play_music(
        self,
        interaction: discord.Interaction,
        url: str,
        loop: bool = False,
    ) -> None:
        """Play a YouTube video or playlist."""
        await interaction.response.defer()

        # Check if guild is valid
        guild_id = interaction.guild_id
        guild = interaction.guild
        if not guild_id or not guild:
            await interaction.followup.send("❌ 이 명령어는 서버에서만 사용할 수 있습니다.")
            return

        # Check if user is in a voice channel
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 음성 채널에 접속해야 합니다.")
            return

        try:
            await interaction.followup.send(f"🎵 재생 중... {url}")
            tracks = await self.service.play_music(
                guild_id,
                interaction.user.voice.channel,
                url,
                loop=loop,
            )

            # Show result
            if len(tracks) == 1:
                msg = f"✅ 재생 중: **{tracks[0].title}**"
            else:
                msg = f"✅ {len(tracks)}개 곡을 재생합니다"

            if loop:
                msg += " (반복 모드)"

            await interaction.followup.send(msg)
            log.info("play_music: %d tracks from %r (loop=%s)", len(tracks), url, loop)

        except YoutubeError as exc:
            error_msg = f"❌ YouTube 오류: {exc}"
            log.error(error_msg)
            await interaction.followup.send(error_msg)
            await self._log_to_channel(guild, "error", error_msg)

        except VoiceException as exc:
            error_msg = f"❌ 음성 연결 오류: {exc}"
            log.error(error_msg)
            await interaction.followup.send(error_msg)
            await self._log_to_channel(guild, "error", error_msg)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await interaction.followup.send(error_msg)
            await self._log_to_channel(guild, "error", error_msg)

    @app_commands.command(name="스킵", description="현재 재생 중인 곡을 건너뜁니다.")
    async def skip(self, interaction: discord.Interaction) -> None:
        """Skip the currently playing track and play the next one."""
        await interaction.response.defer()

        # Check if guild is valid
        guild_id = interaction.guild_id
        guild = interaction.guild
        if not guild_id or not guild:
            await interaction.followup.send("❌ 이 명령어는 서버에서만 사용할 수 있습니다.")
            return

        try:
            next_title = await self.service.skip(guild_id)

            if next_title:
                await interaction.followup.send(f"⏭️ 다음 곡: **{next_title}**")
            else:
                await interaction.followup.send("⏭️ 다음 곡이 없습니다.")

        except VoiceException as exc:
            error_msg = f"❌ {exc}"
            log.warning(error_msg)
            await interaction.followup.send(error_msg)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await interaction.followup.send(error_msg)
            await self._log_to_channel(guild, "error", error_msg)

    @app_commands.command(name="나가", description="음악 재생을 중지하고 음성 채널을 나갑니다.")
    async def stop(self, interaction: discord.Interaction) -> None:
        """Stop all playback and leave the voice channel."""
        await interaction.response.defer()

        # Check if guild is valid
        guild_id = interaction.guild_id
        guild = interaction.guild
        if not guild_id or not guild:
            await interaction.followup.send("❌ 이 명령어는 서버에서만 사용할 수 있습니다.")
            return

        try:
            await self.service.stop(guild_id)
            await interaction.followup.send("⏹️ 재생을 중지했습니다.")

        except VoiceException as exc:
            error_msg = f"❌ {exc}"
            log.warning(error_msg)
            await interaction.followup.send(error_msg)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await interaction.followup.send(error_msg)
            await self._log_to_channel(guild, "error", error_msg)

    @app_commands.command(name="재생정보", description="현재 재생 중인 곡과 큐 정보를 표시합니다.")
    async def queue(self, interaction: discord.Interaction) -> None:
        """Display the current playback status and queue information."""
        await interaction.response.defer()

        # Check if guild is valid
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("❌ 이 명령어는 서버에서만 사용할 수 있습니다.")
            return

        try:
            info = self.service.get_queue_info(guild_id)

            if not info["is_playing"] and info["queue_size"] == 0:
                await interaction.followup.send("현재 재생 중인 음악이 없습니다.")
                return

            # Build status message
            lines = []

            if info["current_track"]:
                lines.append(f"**현재 곡**: {info['current_track']}")
            else:
                lines.append("**현재 곡**: 없음")

            if info["looping"]:
                lines.append("**모드**: 반복 중")

            if info["queue_size"] > 0:
                lines.append(f"**대기 중**: {info['queue_size']}개 곡")
            else:
                lines.append("**대기 중**: 없음")

            status = "\n".join(lines)
            embed = discord.Embed(
                title="🎵 재생 정보",
                description=status,
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await interaction.followup.send(error_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _log_to_channel(
        self,
        guild: discord.Guild,
        level: str,
        message: str,
    ) -> None:
        """Send a log message to the configured log channel.

        Args:
            guild: The Discord guild.
            level: Log level (info, warning, error).
            message: The message to log.
        """
        if not self.service._log_channel_id:
            return

        try:
            channel = self.bot.get_channel(self.service._log_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}
                icon = emoji.get(level, "📝")
                await channel.send(f"{icon} **{guild.name}**: {message}")
        except Exception as exc:
            log.debug("Failed to log to log channel: %s", exc)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(MusicCog(bot))
