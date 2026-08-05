"""Alembic migration template configuration."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Apply the initial schema."""
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "users",
        sa.Column(
            "discord_id",
            sa.BigInteger(),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "voice_settings",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column("voice_id", sa.String(length=255), nullable=False),
    )
    op.create_table(
        "minecraft_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("folder", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
    )
    op.create_unique_constraint("uq_minecraft_servers_alias", "minecraft_servers", ["alias"])
    op.create_table(
        "minecraft_users",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column("minecraft_uuid", sa.String(length=36), nullable=False),
    )
    op.create_unique_constraint("uq_minecraft_users_uuid", "minecraft_users", ["minecraft_uuid"])


def downgrade() -> None:
    """Reverse the initial schema."""
    op.drop_table("minecraft_users")
    op.drop_table("minecraft_servers")
    op.drop_table("voice_settings")
    op.drop_table("users")
