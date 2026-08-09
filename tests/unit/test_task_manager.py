"""Tests for the background task manager (core/task_manager.py)."""

from __future__ import annotations

import asyncio

import pytest

from core.task_manager import TaskManager


@pytest.mark.asyncio
async def test_create_task_runs_and_untracks_itself() -> None:
    manager = TaskManager()
    done = asyncio.Event()

    async def worker() -> None:
        done.set()

    task = manager.create_task(worker(), name="worker")
    assert task in manager.tasks

    await asyncio.wait_for(done.wait(), timeout=1)
    await task
    await asyncio.sleep(0)  # let the done callback run

    # Finished tasks are removed via the done callback.
    assert task not in manager.tasks


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_tasks() -> None:
    manager = TaskManager()
    started = asyncio.Event()

    async def long_worker() -> None:
        started.set()
        await asyncio.sleep(3600)

    manager.create_task(long_worker(), name="long")
    await asyncio.wait_for(started.wait(), timeout=1)

    await manager.shutdown(timeout=2)

    assert manager.tasks == set()


@pytest.mark.asyncio
async def test_shutdown_is_noop_when_empty() -> None:
    manager = TaskManager()

    await manager.shutdown(timeout=1)

    assert manager.tasks == set()


@pytest.mark.asyncio
async def test_cancel_all_requests_cancellation() -> None:
    manager = TaskManager()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    manager.create_task(worker(), name="c")
    await asyncio.wait_for(started.wait(), timeout=1)
    manager.cancel_all()

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.sleep(0)  # let the done callback run
    assert manager.tasks == set()
