"""Discord bot construction and lifecycle helpers."""

from __future__ import annotations

from discord import Intents
from discord.ext import commands

from core.logger import get_logger

log = get_logger(__name__)


def build_intents() -> Intents:
    """Enable the intents required by the bot.

    Returns:
        A configured :class:`Intents` instance.
    """
    intents = Intents.default()
    intents.message_content = True
    intents.voice_states = True
    return intents


def create_bot(prefix: str, *, sync_commands: bool = False) -> commands.Bot:
    """Create and configure the bot instance.

    Args:
        prefix: Command prefix, e.g. ``"!"``.
        sync_commands: If True, sync application commands on startup.

    Returns:
        A configured :class:`commands.Bot`.
    """
    bot = commands.Bot(
        command_prefix=prefix,
        intents=build_intents(),
        help_command=None,
    )

    @bot.event
    async def on_ready() -> None:
        log.info("logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")
        if sync_commands:
            await bot.tree.sync()

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        message = str(error) or error.__class__.__name__
        await ctx.send(f"오류가 발생했습니다: {message}")
        if isinstance(error, commands.CommandError) and not isinstance(
            error,
            (commands.CommandNotFound, commands.UserInputError),
        ):
            log.error("command %s failed: %s", ctx.command, message, exc_info=error)

    return bot


async def load_cogs(bot: commands.Bot, cogs: list[str]) -> None:
    """Load the given cog modules onto the bot.

    Args:
        bot: The bot instance.
        cogs: Fully-qualified cog module paths.
    """
    for path in cogs:
        await bot.load_extension(path)
        log.info("loaded cog %s", path)
