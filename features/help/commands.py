"""Help command — displays documentation for all bot commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext.commands import Bot

from core.logger import get_logger
from features.base import FeatureCog

log = get_logger(__name__)


# Help documentation for each feature
HELP_DATA = {
    "minecraft": {
        "title": "🎮 마인크래프트 서버 관리",
        "emoji": "🎮",
        "commands": [
            {
                "name": "/마크_생성",
                "args": "<서버이름> [포트]",
                "description": "새로운 마인크래프트 서버를 생성합니다. (관리자 권한 필요)",
            },
            {
                "name": "/마크_켜",
                "args": "<서버이름>",
                "description": "마인크래프트 서버를 시작합니다.",
            },
            {
                "name": "/마크_꺼",
                "args": "<서버이름>",
                "description": "마인크래프트 서버를 종료합니다.",
            },
            {
                "name": "/마크_상태",
                "args": "<서버이름>",
                "description": "마인크래프트 서버의 상태를 확인합니다.",
            },
            {
                "name": "/마크_주소",
                "args": "<서버이름> [external|internal]",
                "description": "서버 접속 주소를 표시합니다.",
            },
            {
                "name": "/마크_유저확인",
                "args": "<서버이름>",
                "description": "현재 접속 중인 플레이어를 확인합니다.",
            },
            {
                "name": "/마크_명령어",
                "args": "<서버이름> <명령어>",
                "description": "서버에 RCON 명령어를 실행합니다. (OP 권한 필요)",
            },
            {
                "name": "/마크_서버",
                "args": "",
                "description": "모든 마인크래프트 서버의 목록을 표시합니다.",
            },
            {
                "name": "/마크_로그",
                "args": "<서버이름>",
                "description": "서버의 최근 로그를 표시합니다.",
            },
            {
                "name": "/마크_화이트리스트",
                "args": "<서버이름> <add|remove> <닉네임/멘션>",
                "description": "화이트리스트 관리 (Discord 닉네임/멘션 또는 Minecraft 닉네임)",
            },
            {
                "name": "/마크_전체화이트리스트",
                "args": "<서버이름>",
                "description": "등록된 모든 유저를 기존 서버 화이트리스트에 일괄 추가 (관리자)",
            },
            {
                "name": "/마크_등록",
                "args": "<UUID>",
                "description": "내 Minecraft UUID 등록 → 전체 서버 화이트리스트 자동 추가",
            },
            {
                "name": "/마크_uuid등록",
                "args": "<유저> <UUID>",
                "description": "Discord 유저와 Minecraft UUID를 연결합니다. (관리자 권한 필요)",
            },
        ],
    },
    "tts": {
        "title": "🎙️ TTS 음성 기능",
        "emoji": "🎙️",
        "commands": [
            {
                "name": "/tts_입장",
                "args": "",
                "description": "음성 채널에 입장하고 메시지를 읽기 시작합니다.",
            },
            {
                "name": "/tts_나가기",
                "args": "",
                "description": "음성 채널에서 나갑니다.",
            },
            {
                "name": "/voice",
                "args": "<음성ID>",
                "description": "TTS 음성 목소리를 설정합니다.",
            },
        ],
    },
    "meal": {
        "title": "🍱 급식 정보",
        "emoji": "🍱",
        "commands": [
            {
                "name": "/급식",
                "args": "",
                "description": "오늘의 급식을 조회·출력합니다. (디버깅/통합 테스트)",
            },
            {
                "name": "/급식날짜",
                "args": "[년도] [월] [일]",
                "description": "특정 날짜의 급식을 조회·출력합니다. (디버깅/통합 테스트)",
            },
        ],
    },
    "music": {
        "title": "🎵 음악 재생",
        "emoji": "🎵",
        "commands": [
            {
                "name": "/play",
                "args": "<검색어 또는 URL>",
                "description": "음악을 재생합니다.",
            },
            {
                "name": "/skip",
                "args": "",
                "description": "현재 곡을 스킵합니다.",
            },
            {
                "name": "/stop",
                "args": "",
                "description": "재생을 중지하고 음성 채널을 나갑니다.",
            },
            {
                "name": "/queue",
                "args": "",
                "description": "현재 재생 목록을 표시합니다.",
            },
        ],
    },
}


async def category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for help category."""
    categories = list(HELP_DATA.keys())
    filtered = [c for c in categories if c.lower().startswith(current.lower())]
    return [app_commands.Choice(name=cat, value=cat) for cat in filtered]


class HelpCog(FeatureCog):
    """Slash commands for help documentation."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

    @app_commands.command(name="help", description="봇의 모든 커맨드 도움말을 표시합니다.")
    @app_commands.describe(category="카테고리 (minecraft/tts/meal/music)")
    @app_commands.autocomplete(category=category_autocomplete)
    async def help_command(
        self,
        interaction: discord.Interaction,
        category: str | None = None,
    ) -> None:
        """Show help for all commands or a specific category."""
        await interaction.response.defer()

        if category and category.lower() in HELP_DATA:
            # Show help for a specific category
            await self._show_category_help(interaction, category.lower())
        else:
            # Show overview of all categories
            await self._show_all_help(interaction)

    async def _show_all_help(self, interaction: discord.Interaction) -> None:
        """Display overview of all command categories."""
        embed = discord.Embed(
            title="🤖 Plaything Bot - 커맨드 도움말",
            description="사용 가능한 커맨드 카테고리를 선택하세요.",
            color=discord.Color.blue(),
        )

        for category, data in HELP_DATA.items():
            cmd_count = len(data["commands"])
            embed.add_field(
                name=f"{data['emoji']} {data['title']}",
                value=f"`/help {category}` - {cmd_count}개 커맨드",
                inline=False,
            )

        embed.set_footer(text="각 카테고리를 확인하려면 /help <카테고리>를 입력하세요.")
        await interaction.followup.send(embed=embed)

    async def _show_category_help(
        self,
        interaction: discord.Interaction,
        category: str,
    ) -> None:
        """Display detailed help for a specific category."""
        if category not in HELP_DATA:
            await interaction.followup.send(f"알 수 없는 카테고리: {category}")
            return

        data = HELP_DATA[category]
        embed = discord.Embed(
            title=f"{data['emoji']} {data['title']}",
            description=f"{len(data['commands'])}개의 커맨드가 있습니다.",
            color=discord.Color.green(),
        )

        for cmd in data["commands"]:
            # Format command with arguments
            if cmd["args"]:
                cmd_text = f"`{cmd['name']} {cmd['args']}`"
            else:
                cmd_text = f"`{cmd['name']}`"

            embed.add_field(
                name=cmd_text,
                value=cmd["description"],
                inline=False,
            )

        embed.set_footer(
            text=f"/help를 입력하여 다른 카테고리를 보거나 {category} 카테고리로 돌아오기"
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(HelpCog(bot))
