"""Tests for Database.ping (health check) with a fake pool."""

from __future__ import annotations

import pytest

from core.database import Database


class FakeConn:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    async def fetchval(self, query: str):
        if not self._ok:
            raise OSError("connection refused")
        return 1


class FakeAcquire:
    def __init__(self, ok: bool) -> None:
        self._conn = FakeConn(ok)

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakePool:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self._ok)


@pytest.mark.asyncio
async def test_ping_returns_true_when_reachable() -> None:
    db = Database("postgresql://user:pass@localhost/db")
    db._pool = FakePool(ok=True)

    assert await db.ping() is True


@pytest.mark.asyncio
async def test_ping_returns_false_on_connection_error() -> None:
    db = Database("postgresql://user:pass@localhost/db")
    db._pool = FakePool(ok=False)

    assert await db.ping() is False


@pytest.mark.asyncio
async def test_ping_returns_false_without_pool() -> None:
    db = Database("postgresql://user:pass@localhost/db")

    assert await db.ping() is False
