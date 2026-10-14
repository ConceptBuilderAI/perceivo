"""One history record per run, fed by the events the executor emits.

:func:`recorded_run` opens a run: it installs a sink that appends every event
to the run's record (and passes it on to any outer listener, such as
``run_stream``), emits the opening ``log`` event, and hands back a
:class:`RunRecorder`. :meth:`RunRecorder.finish` emits the closing ``error`` (only
when no node already reported one) and ``complete`` events, then writes the
record once, under ``<home>/runs/``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from .._internal.config_discovery import discover_config
from ._emit import emit, listen
from .events import EventType, ExecutionEvent
from .history import RunHistory, RunRecord
from .types import ExecutionResult

_PathLike = Union[str, "os.PathLike[str]"]


_RUN_ID = "run_id"


def run_config_path(start: _PathLike, config_path: Optional[str]) -> Optional[str]:
    """The ``perceivo.yaml`` a run reads: ``config_path``, else the nearest at or above ``start``.

    ``PERCEIVO_CONFIG`` wins over the upward walk, as it does for public-API calls.
    ``None`` means no file applies, and the configuration defaults are used.
    """
    if config_path:
        return config_path
    found = discover_config(start)
    return str(found) if found is not None else None


def orchestration_name(project_dir: _PathLike) -> str:
    """The ``name`` in ``orchestration.yaml``, else the directory name."""
    path = Path(project_dir).resolve()
    try:
        data: Any = yaml.safe_load((path / "orchestration.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        data = None
    name = data.get("name") if isinstance(data, dict) else None
    return str(name) if name else path.name


class RunRecorder:
    """Collects one run's events and writes its record once."""

    def __init__(self, history: RunHistory, record: RunRecord):
        self._history = history
        self.record = record
        self._saw_error = False

    def on_event(self, event: ExecutionEvent) -> None:
        if event.event_type is EventType.ERROR:
            self._saw_error = True
        self._history.add_event(self.record, event)

    def finish(self, result: ExecutionResult) -> None:
        """Emit the closing events and write the record."""
        if not result.success and not self._saw_error:
            emit(ExecutionEvent.error(result.error or "Run failed"))
        emit(ExecutionEvent.complete(success=result.success, data={_RUN_ID: self.record.run_id}))
        self._history.complete_run(
            self.record,
            success=result.success,
            output_data=dict(result.response_data or {}),
            error=result.error,
        )

    def fail(self, exc: BaseException) -> None:
        """Write the record of a run that raised, then let the exception go on.

        The closing ``error`` and ``complete`` go into the record only: the
        caller that catches the exception (``run_stream``) reports them to its
        own consumer, and emitting them here too would report the run twice.
        """
        self._history.add_event(self.record, ExecutionEvent.error(str(exc)))
        closing = ExecutionEvent.complete(success=False, data={_RUN_ID: self.record.run_id})
        self._history.add_event(self.record, closing)
        self._history.complete_run(self.record, success=False, error=str(exc))


@contextmanager
def recorded_run(
    start: _PathLike,
    *,
    name: str,
    kind: str,
    input_data: Optional[dict[str, Any]],
    config_path: Optional[str],
) -> Iterator[RunRecorder]:
    """Open a recorded run whose events are collected until the block exits.

    A run that raises still writes its one record, as failed, before the
    exception propagates. The opening ``log`` event carries ``data["run_id"]``.
    """
    history = RunHistory(start, config_path=config_path)
    recorder = RunRecorder(history, history.start_run(name, input_data, kind=kind))
    run_id = recorder.record.run_id
    with listen(recorder.on_event):
        opening = ExecutionEvent.log(f"Starting {kind}: {name} (run_id={run_id})")
        opening.data[_RUN_ID] = run_id
        emit(opening)
        try:
            yield recorder
        except Exception as exc:
            recorder.fail(exc)
            raise
