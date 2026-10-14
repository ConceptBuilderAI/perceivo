"""Where the executor's execution events go while a run is in progress.

The executor calls :func:`emit` as nodes actually run. A run installs a sink
with :func:`listen`; sinks nest, so ``run_stream`` listens around a run that
itself listens to record history, and each event reaches both. The sink lives in
a :class:`~contextvars.ContextVar`, so nested executors (inline and subprocess
children) and invoke-port branches running as tasks of the same run reach the
same sink without being handed it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from .events import ExecutionEvent

Sink = Callable[[ExecutionEvent], None]

_SINK: ContextVar[Optional[Sink]] = ContextVar("perceivo_run_event_sink", default=None)


def emit(event: ExecutionEvent) -> None:
    """Hand ``event`` to the active sink, if a run is listening."""
    sink = _SINK.get()
    if sink is not None:
        sink(event)


@contextmanager
def listen(sink: Sink) -> Iterator[None]:
    """Route events to ``sink``, then on to any sink already listening."""
    outer = _SINK.get()

    def chained(event: ExecutionEvent) -> None:
        sink(event)
        if outer is not None:
            outer(event)

    token = _SINK.set(chained)
    try:
        yield
    finally:
        _SINK.reset(token)
