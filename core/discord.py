"""Discord bot construction and lifecycle helpers.

Lifecycle events (ready / reconnect / disconnect / exceptions) are emitted as
structured log events so they appear in the Discord log channel as embeds.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import Intents, app_commands
from discord.ext import commands

from core.logger import get_logger, log_event

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


def create_bot(
    prefix: str,
    *,
    sync_commands: bool = False,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> commands.Bot:
    """Create and configure the bot instance.

    Args:
        prefix: Command prefix, e.g. ``"!"``.
        sync_commands: If True, sync application commands on startup.
        on_ready: Optional coroutine invoked once after the bot becomes ready
            (used, for example, to start the scheduler — Bot Ready → Scheduler
            Start).

    Returns:
        A configured :class:`commands.Bot`.
    """
    bot = commands.Bot(
        command_prefix=prefix,
        intents=build_intents(),
        help_command=None,
    )

    # Capture the on_ready callback BEFORE the event handler below is bound to
    # the same name. Otherwise ``on_ready`` inside the handler resolves to the
    # handler itself and ``await on_ready()`` recurses forever.
    ready_callback = on_ready

    synced = False

    @bot.event
    async def on_ready() -> None:
        nonlocal synced
        user = bot.user
        log_event(
            log,
            "Bot Ready",
            details={
                "user": str(user) if user else "unknown",
                "id": str(user.id) if user else "?",
            },
        )
        # Sync application commands exactly once per process. Re-running this on
        # every websocket reconnect re-registers (and can churn) the global
        # command definitions, which makes clients reject in-flight invocations
        # with "This command is outdated" and burns Discord's per-day update
        # budget (200 for global commands).
        if sync_commands and not synced:
            synced = True
            log.info("syncing %d application commands", len(bot.tree._get_all_commands()))
            await bot.tree.sync()
            log.info("application commands synced")
        if ready_callback is not None:
            await ready_callback()

    @bot.event
    async def on_resumed() -> None:
        log_event(log, "Bot Reconnected", level=logging.WARNING)

    @bot.event
    async def on_disconnect() -> None:
        log_event(log, "Bot Disconnected", level=logging.WARNING)

    @bot.event
    async def on_error(event_method: str, *args, **kwargs) -> None:
        error = args[0] if args else None
        log_event(
            log,
            "Bot Exception",
            level=logging.ERROR,
            details={"event": event_method, "error": str(error) if error else "unknown"},
        )

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        message = str(error) or error.__class__.__name__
        if isinstance(error, commands.CheckFailure):
            await ctx.send("⚠️ 관리자 권한이 필요합니다.")
            return
        await ctx.send(f"오류가 발생했습니다: {message}")
        if isinstance(error, commands.CommandError) and not isinstance(
            error,
            (commands.CommandNotFound, commands.UserInputError),
        ):
            log.error("command %s failed: %s", ctx.command, message, exc_info=error)

    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Handle slash-command failures (permission checks, unexpected errors)."""
        if isinstance(error, app_commands.errors.CheckFailure):
            message = "⚠️ 관리자 권한이 필요합니다."
        else:
            message = f"오류가 발생했습니다: {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:  # pragma: no cover - defensive
            pass
        if not isinstance(error, app_commands.errors.CheckFailure):
            log.error(
                "app command %s failed: %s",
                interaction.command,
                error,
                exc_info=error,
            )

    return bot


async def load_cogs(bot: commands.Bot, cogs: list[str]) -> None:
    """Load the given cog modules onto the bot.

    Args:
        bot: The bot instance.
        cogs: Fully-qualified cog module paths.
    """
    for path in cogs:
        try:
            await bot.load_extension(path)
            log.info("loaded cog %s", path)
        except Exception as exc:
            log.error("failed to load cog %s: %s", path, exc, exc_info=exc)
            raise
