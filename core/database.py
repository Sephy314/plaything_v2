"""PostgreSQL connection management.

A single shared connection pool is created at startup and injected into
repositories via constructor dependency injection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from core.exceptions import DatabaseException
from core.logger import get_logger

log = get_logger(__name__)


class Database:
    """Owns the process-wide asyncpg connection pool."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        """Return the active connection pool.

        Raises:
            DatabaseException: If the pool has not been initialized.
        """
        if self._pool is None:
            raise DatabaseException("Database pool is not initialized")
        return self._pool

    async def connect(self, min_size: int = 5, max_size: int = 20) -> None:
        """Create (or recreate) the connection pool and verify connectivity.

        Args:
            min_size: Minimum pool size.
            max_size: Maximum pool size.

        Raises:
            DatabaseException: If the connection or connectivity check fails.
        """
        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=min_size,
                max_size=max_size,
                command_timeout=60,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise DatabaseException(f"Failed to create database pool: {exc}") from exc

        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Database connectivity check failed: {exc}") from exc

        log.info("database connected")

    async def close(self) -> None:
        """Gracefully close the connection pool, if present."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
        log.info("database disconnected")

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Execute a query and return a single value.

        Args:
            query: SQL template with ``$1`` placeholder style.
            *args: Query parameters.

        Returns:
            A single value from the first column of the first row.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Execute a query and return a single row.

        Args:
            query: SQL template.
            *args: Query parameters.

        Returns:
            The matching row or ``None``.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Execute a query and return all matching rows.

        Args:
            query: SQL template.
            *args: Query parameters.

        Returns:
            A list of rows.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        """Execute a query with no expected result set.

        Args:
            query: SQL template.
            *args: Query parameters.

        Returns:
            The command status string.
        """
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    @asynccontextmanager
    async def transaction(self):
        """Yield a connection with an open transaction.

        Used when a set of writes must succeed or fail atomically. The
        transaction is committed on success and rolled back on error.

        Yields:
            An ``asyncpg.Connection`` inside an active transaction.

        Raises:
            DatabaseException: If acquiring the connection fails.
        """
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    yield conn
        except asyncpg.PostgresError as exc:
            raise DatabaseException(f"Transaction failed: {exc}") from exc
