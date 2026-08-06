"""Background task registry.

Centralises the tracking of long-running :class:`asyncio.Task` objects so the
application can cancel and drain them during a graceful shutdown instead of
leaving orphaned coroutines behind.

Usage::

    task_manager = TaskManager()
    task_manager.create_task(self._monitor(server), name="mc-monitor")
    ...
    await task_manager.shutdown()   # cancels + awaits every tracked task
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any

from core.logger import get_logger

log = get_logger(__name__)


class TaskManager:
    """Tracks asyncio tasks and cancels them on shutdown."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def tasks(self) -> set[asyncio.Task[Any]]:
        """Return the set of currently tracked tasks."""
        return self._tasks

    def create_task(
        self,
        coro: Coroutine[Any, Any, Any] | Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        """Schedule a coroutine and track it until completion.

        Args:
            coro: The coroutine (or awaitable) to run in the background.
            name: Optional task name for logging.

        Returns:
            The created :class:`asyncio.Task`.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def cancel_all(self) -> None:
        """Request cancellation of every tracked task (non-blocking)."""
        for task in list(self._tasks):
            task.cancel()

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel all tracked tasks and wait for them to finish.

        Args:
            timeout: Maximum seconds to wait for tasks to stop.
        """
        pending = list(self._tasks)
        if not pending:
            return
        for task in pending:
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
        except TimeoutError:
            log.warning("timed out waiting for %d background tasks to stop", len(pending))
        self._tasks.clear()
        log.info("cancelled %d background tasks", len(pending))
