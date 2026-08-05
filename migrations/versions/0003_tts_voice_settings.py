"""Add per-user TTS voice settings.

Creates the ``tts_voice_settings`` table holding the configured TTS voice
and the last-detected language per Discord user.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Apply the TTS voice settings schema."""
    op.create_table(
        "tts_voice_settings",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column("voice_id", sa.String(length=255), nullable=False, server_default="default"),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
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


def downgrade() -> None:
    """Reverse the TTS voice settings schema."""
    op.drop_table("tts_voice_settings")
