"""Music feature commands — prefix command group ``!재생해``, ``!스킵``, ``!나가``.

Provides YouTube video and playlist playback via Discord voice channels.

Supports:
- YouTube video URLs and playlist URLs
- Queue management with automatic advancement
- Loop (repeat) mode via the "계속" (continue) parameter
- Skip to next track
- Stop and leave voice channel

All playback is coordinated through the shared AudioManager, which mixes
YouTube tracks with TTS output so both can play simultaneously.
"""

from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands
from discord.ext.commands import Bot, Context

from core.exceptions import VoiceException, YoutubeError
from core.logger import get_logger
from features.base import FeatureCog
from features.music.service import MusicService

log = get_logger(__name__)


class MusicCog(FeatureCog):
    """Prefix commands for YouTube music playback."""

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

    @commands.command(name="재생해", description="YouTube 영상 또는 플레이리스트를 재생합니다.")
    async def play_music(self, ctx: Context, url: str, *args: str) -> None:
        """Play a YouTube video or playlist.

        Usage:
            !재생해 https://youtube.com/watch?v=xxxxx
            !재생해 https://youtube.com/playlist?list=xxxxx 계속

        Args:
            url: A YouTube video or playlist URL.
            args: Optional "계속" flag to enable loop mode.
        """
        # Check if user is in a voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ 음성 채널에 접속해야 합니다.")
            return

        # Parse loop flag
        loop = "계속" in args

        try:
            await ctx.send(f"🎵 재생 중... {url}")
            tracks = await self.service.play_music(
                ctx.guild.id,
                ctx.author.voice.channel,
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

            await ctx.send(msg)
            log.info("play_music: %d tracks from %r (loop=%s)", len(tracks), url, loop)

        except YoutubeError as exc:
            error_msg = f"❌ YouTube 오류: {exc}"
            log.error(error_msg)
            await ctx.send(error_msg)
            await self._log_to_channel(ctx.guild, "error", error_msg)

        except VoiceException as exc:
            error_msg = f"❌ 음성 연결 오류: {exc}"
            log.error(error_msg)
            await ctx.send(error_msg)
            await self._log_to_channel(ctx.guild, "error", error_msg)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await ctx.send(error_msg)
            await self._log_to_channel(ctx.guild, "error", error_msg)

    @commands.command(name="스킵", description="현재 재생 중인 곡을 건너뜁니다.")
    async def skip(self, ctx: Context) -> None:
        """Skip the currently playing track and play the next one.

        Usage:
            !스킵
        """
        try:
            next_title = await self.service.skip(ctx.guild.id)

            if next_title:
                await ctx.send(f"⏭️ 다음 곡: **{next_title}**")
            else:
                await ctx.send("⏭️ 다음 곡이 없습니다.")

        except VoiceException as exc:
            error_msg = f"❌ {exc}"
            log.warning(error_msg)
            await ctx.send(error_msg)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await ctx.send(error_msg)
            await self._log_to_channel(ctx.guild, "error", error_msg)

    @commands.command(name="나가", description="음악 재생을 중지하고 음성 채널을 나갑니다.")
    async def stop(self, ctx: Context) -> None:
        """Stop all playback and leave the voice channel.

        Usage:
            !나가
        """
        try:
            await self.service.stop(ctx.guild.id)
            await ctx.send("⏹️ 재생을 중지했습니다.")

        except VoiceException as exc:
            error_msg = f"❌ {exc}"
            log.warning(error_msg)
            await ctx.send(error_msg)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await ctx.send(error_msg)
            await self._log_to_channel(ctx.guild, "error", error_msg)

    @commands.command(name="재생정보", description="현재 재생 중인 곡과 큐 정보를 표시합니다.")
    async def queue(self, ctx: Context) -> None:
        """Display the current playback status and queue information.

        Usage:
            !재생정보
        """
        try:
            info = self.service.get_queue_info(ctx.guild.id)

            if not info["is_playing"] and info["queue_size"] == 0:
                await ctx.send("현재 재생 중인 음악이 없습니다.")
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
            await ctx.send(embed=embed)

        except Exception as exc:
            error_msg = f"❌ 예상치 못한 오류: {exc}"
            log.error(error_msg, exc_info=exc)
            await ctx.send(error_msg)

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
