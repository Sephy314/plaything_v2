"""Tests for the admin permission helpers (core/permissions.py)."""

from __future__ import annotations

import pytest

from core.exceptions import PermissionDenied
from core.permissions import admin_only, is_admin, require_admin


class FakePermissions:
    def __init__(self, administrator: bool = False) -> None:
        self.administrator = administrator


class FakeMember:
    def __init__(self, member_id: int, administrator: bool = False) -> None:
        self.id = member_id
        self.guild_permissions = FakePermissions(administrator)


class FakeGuild:
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id


class FakeContext:
    """Mimics commands.Context (has ``author``)."""

    def __init__(self, member: FakeMember, guild: FakeGuild) -> None:
        self.author = member
        self.guild = guild


class FakeInteraction:
    """Mimics discord.Interaction (has ``user``)."""

    def __init__(self, member: FakeMember, guild: FakeGuild) -> None:
        self.user = member
        self.guild = guild


def test_is_admin_true_for_administrator() -> None:
    guild = FakeGuild(owner_id=999)
    ctx = FakeContext(FakeMember(1, administrator=True), guild)

    assert is_admin(ctx) is True


def test_is_admin_true_for_guild_owner() -> None:
    guild = FakeGuild(owner_id=1)
    ctx = FakeContext(FakeMember(1, administrator=False), guild)

    assert is_admin(ctx) is True


def test_is_admin_false_for_regular_member() -> None:
    guild = FakeGuild(owner_id=999)
    ctx = FakeContext(FakeMember(2, administrator=False), guild)

    assert is_admin(ctx) is False


def test_is_admin_works_with_interaction() -> None:
    guild = FakeGuild(owner_id=999)
    interaction = FakeInteraction(FakeMember(3, administrator=True), guild)

    assert is_admin(interaction) is True


def test_is_admin_false_without_guild() -> None:
    member = FakeMember(1, administrator=True)
    ctx = FakeContext(member, None)

    assert is_admin(ctx) is False


def test_admin_only_registers_both_check_types() -> None:
    @admin_only()
    async def sample_command(self, interaction) -> None:
        """Dummy command."""

    # discord.py stores checks on these attributes for prefix and slash paths.
    assert getattr(sample_command, "__commands_checks__", None)
    assert getattr(sample_command, "__discord_app_commands_checks__", None)


@pytest.mark.asyncio
async def test_admin_only_prefix_check_rejects_regular_member() -> None:
    @admin_only()
    async def sample_command(self, ctx) -> None:
        """Dummy command."""

    guild = FakeGuild(owner_id=999)
    ctx = FakeContext(FakeMember(2, administrator=False), guild)
    checks = getattr(sample_command, "__commands_checks__", [])
    assert checks
    assert all(check(ctx) is False for check in checks)


@pytest.mark.asyncio
async def test_admin_only_prefix_check_accepts_administrator() -> None:
    @admin_only()
    async def sample_command(self, ctx) -> None:
        """Dummy command."""

    guild = FakeGuild(owner_id=999)
    ctx = FakeContext(FakeMember(2, administrator=True), guild)
    checks = getattr(sample_command, "__commands_checks__", [])
    assert checks
    assert all(check(ctx) is True for check in checks)


def test_require_admin_raises_for_regular_member() -> None:
    guild = FakeGuild(owner_id=999)
    ctx = FakeContext(FakeMember(2, administrator=False), guild)

    with pytest.raises(PermissionDenied):
        require_admin(ctx)


def test_require_admin_passes_for_administrator() -> None:
    guild = FakeGuild(owner_id=999)
    ctx = FakeContext(FakeMember(2, administrator=True), guild)

    require_admin(ctx)  # should not raise
