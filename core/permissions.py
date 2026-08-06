"""Permission helpers shared by all cogs.

Provides the :func:`admin_only` decorator used to gate commands behind the
Discord **Administrator** permission (or the guild owner). The decorator works
for both prefix commands (``commands.check``) and slash commands
(``app_commands.check``) because it attaches the same predicate to both
discord.py check slots.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from discord import app_commands
from discord.ext import commands

from core.exceptions import PermissionDenied
from core.logger import get_logger

log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def is_admin(ctx_or_interaction: Any) -> bool:
    """Return whether the invoking user is a guild administrator or owner.

    Works with both :class:`commands.Context` and :class:`discord.Interaction`.

    Args:
        ctx_or_interaction: The command context or interaction.

    Returns:
        True when the user holds Administrator permission (or is the owner).
    """
    member = getattr(ctx_or_interaction, "author", None) or getattr(
        ctx_or_interaction, "user", None
    )
    guild = getattr(ctx_or_interaction, "guild", None)
    if member is None or guild is None:
        return False
    if member.id == guild.owner_id:
        return True
    return bool(
        getattr(member, "guild_permissions", None) and member.guild_permissions.administrator
    )


def admin_only() -> Callable[[F], F]:
    """Gate a command to Discord administrators.

    Usage::

        @admin_only()
        @app_commands.command(name="봇_종료", description="...")
        async def cmd(self, interaction: discord.Interaction) -> None:
            ...

    The decorator registers the check for both the prefix-command system and
    the slash-command system, so the same function can be registered either way.
    """

    def decorator(func: F) -> F:
        app_commands.check(is_admin)(func)
        commands.check(is_admin)(func)
        return func

    return decorator


def require_admin(ctx_or_interaction: Any) -> None:
    """Raise :class:`PermissionDenied` unless the user is an administrator.

    Useful inside command bodies when a check decorator is not sufficient
    (e.g. sub-actions of a larger command).

    Args:
        ctx_or_interaction: The command context or interaction.

    Raises:
        PermissionDenied: If the user is not an administrator.
    """
    if not is_admin(ctx_or_interaction):
        log.warning(
            "admin-only command denied for user=%s",
            getattr(
                getattr(ctx_or_interaction, "author", None)
                or getattr(ctx_or_interaction, "user", None),
                "id",
                None,
            ),
        )
        raise PermissionDenied("관리자 권한이 필요합니다.")
