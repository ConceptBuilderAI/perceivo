"""flush_pending_writes returns once every write is done, even when a finished
task's done callback has not run yet.

From Python 3.12, gather() over futures that are already done completes without
yielding to the loop. A finished task whose discard callback is still queued
then stayed in the set forever, and the flush spun on it: CI's Python 3.12 and
3.13 unit jobs hung in the parallel-executor template run.
"""

from __future__ import annotations

import asyncio
import threading

from perceivo.log import _logger


def _flush_in_thread(prepare) -> bool:
    """Run one flush on a fresh loop in a daemon thread; True if it returned."""
    done = threading.Event()

    def main() -> None:
        async def go() -> None:
            prepare(asyncio.get_running_loop())
            await _logger.flush_pending_writes()

        asyncio.run(go())
        done.set()

    threading.Thread(target=main, daemon=True).start()
    return done.wait(timeout=10)


def test_a_finished_write_whose_callback_has_not_run_does_not_hang_the_flush():
    kept: list[asyncio.Future[None]] = []

    def prepare(loop: asyncio.AbstractEventLoop) -> None:
        # A finished write whose discard callback was never run.
        fut: asyncio.Future[None] = loop.create_future()
        fut.set_result(None)
        kept.append(fut)
        _logger._background_tasks.add(fut)  # type: ignore[arg-type]

    try:
        assert _flush_in_thread(prepare), "flush_pending_writes did not return"
        assert kept[0] not in _logger._background_tasks
    finally:
        _logger._background_tasks.difference_update(kept)  # type: ignore[arg-type]


def test_a_write_still_in_flight_is_awaited():
    ran: list[str] = []

    def prepare(loop: asyncio.AbstractEventLoop) -> None:
        async def write() -> None:
            await asyncio.sleep(0.05)
            ran.append("written")

        task = loop.create_task(write())
        _logger._background_tasks.add(task)
        task.add_done_callback(_logger._background_tasks.discard)

    assert _flush_in_thread(prepare)
    assert ran == ["written"]
