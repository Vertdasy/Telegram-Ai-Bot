"""Background task helper."""

import asyncio

from aibot.state import bg_tasks


def spawn(coro) -> asyncio.Task:
    """Starts a background task and keeps a reference so it isn't garbage-collected."""
    task = asyncio.create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return task
