"""Streaming orchestration execution."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

from ._emit import listen as _listen
from .events import ExecutionEvent

_RUN_ID = "run_id"
#: Marks the end of the run on the queue between the run and the consumer.
_DONE = object()


async def run_stream(
    project_dir: str,
    *,
    input_data: Optional[dict[str, Any]] = None,
    config_path: Optional[str] = None,
) -> AsyncIterator[ExecutionEvent]:
    """Run an orchestration and yield events as they occur.

    This is the streaming equivalent of ``run_orchestration()``. The events are
    the executor's own, emitted as each node actually runs, in execution
    order, with real durations, including nodes reached through invoke ports and
    nested orchestrations. They are handed to the consumer through a queue, so a
    ``node_enter`` arrives while that node is still running.

    The stream opens with a ``log`` event naming the run, and closes with
    ``complete`` (``data["run_id"]`` names the run record). A node that fails
    emits ``error`` for that node first, then ``complete(success=False)``.
    The run's history record is the one ``run_orchestration`` writes; this
    function writes none of its own.

    Args:
        project_dir: Path to the orchestration project directory.
        input_data: Input data passed to the START component.
        config_path: Path to perceivo.yaml configuration.

    Yields:
        :class:`ExecutionEvent` instances.
    """
    from . import run_orchestration

    queue: asyncio.Queue[Any] = asyncio.Queue()

    run_ids: list[str] = []

    def forward(event: ExecutionEvent) -> None:
        if _RUN_ID in event.data and not run_ids:
            run_ids.append(str(event.data[_RUN_ID]))
        queue.put_nowait(event)

    async def drive() -> None:
        try:
            with _listen(forward):
                await run_orchestration(project_dir, input_data=input_data, config_path=config_path)
        except Exception as exc:
            # A run that raised (an unreadable perceivo.yaml, a graph the compiler
            # refuses) still ends the stream the way a failed run does, naming the
            # failed record the run wrote when it has one.
            closing = {"error": str(exc), **({_RUN_ID: run_ids[0]} if run_ids else {})}
            queue.put_nowait(ExecutionEvent.error(str(exc)))
            queue.put_nowait(ExecutionEvent.complete(success=False, data=closing))
        finally:
            queue.put_nowait(_DONE)

    task = asyncio.create_task(drive())
    try:
        while (event := await queue.get()) is not _DONE:
            yield event
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
