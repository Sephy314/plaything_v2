"""Extend Minecraft schema for full server management.

Adds status / ownership / timestamps to ``minecraft_servers``, renames the
folder column to ``folder_path``, adds ``minecraft_sessions`` for player
tracking, and timestamps to ``minecraft_users``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Apply the Minecraft management schema additions."""
    op.alter_column("minecraft_servers", "folder", new_column_name="folder_path")
    op.alter_column("minecraft_servers", "folder_path", type_=sa.String(length=1024))

    op.add_column(
        "minecraft_servers",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="stopped",
        ),
    )
    op.add_column(
        "minecraft_servers",
        sa.Column("created_by", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "minecraft_servers",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "minecraft_servers",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "minecraft_users",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "minecraft_sessions",
        sa.Column(
            "server_id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column("player_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["server_id"], ["minecraft_servers.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    """Reverse the Minecraft management schema additions."""
    op.drop_table("minecraft_sessions")
    op.drop_column("minecraft_users", "created_at")
    op.drop_column("minecraft_servers", "updated_at")
    op.drop_column("minecraft_servers", "created_at")
    op.drop_column("minecraft_servers", "created_by")
    op.drop_column("minecraft_servers", "status")
    op.alter_column("minecraft_servers", "folder_path", new_column_name="folder")
