"""User value object(s)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class User:
    """A Discord user record.

    Attributes:
        discord_id: Discord snowflake id.
        created_at: When the record was created.
        updated_at: When the record was last updated.
    """

    discord_id: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> User:
        """Build a :class:`User` from an ``asyncpg`` record.

        Args:
            row: The database row.

        Returns:
            A populated :class:`User`.
        """
        return cls(
            discord_id=row["discord_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
