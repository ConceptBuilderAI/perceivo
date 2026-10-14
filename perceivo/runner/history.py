"""Local run history: one JSON record per run under ``<home>/runs/``.

``<home>`` is the local-runner root (see ``perceivo.config.Config.root``), so a
run started from any directory of a project, and a read of that project's
history from any other, resolve the same directory. Every run path writes
exactly one record: the executor writes it, and ``run_stream`` reads the
events the executor emits rather than keeping a record of its own.

Reading never creates anything: a project that has never run has no ``runs/``
directory, and its history is empty.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Union

from .._internal.home import subdir as _home_subdir
from .events import ExecutionEvent

_PathLike = Union[str, "os.PathLike[str]"]

_ORCHESTRATION = "orchestration"
_STARTED_AT = "started_at"
_ENCODING = "utf-8"


@dataclass
class RunRecord:
    """Record of a single local run."""

    run_id: str
    orchestration_name: str
    started_at: float
    completed_at: Optional[float] = None
    success: Optional[bool] = None
    input_data: Optional[dict[str, Any]] = None
    output_data: Optional[dict[str, Any]] = None
    events: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    #: ``"orchestration"`` for an orchestration run, ``"component"`` for a
    #: single-component run (``run_component`` / ``perceivo run component``).
    kind: str = _ORCHESTRATION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunHistory:
    """Run records under ``<home>/runs/`` for the project at ``base_dir``."""

    def __init__(self, base_dir: _PathLike = ".", *, config_path: Optional[_PathLike] = None):
        self._dir = _home_subdir("runs", base_dir, config_path=config_path)

    def start_run(
        self,
        orchestration_name: str,
        input_data: Optional[dict[str, Any]] = None,
        *,
        kind: str = _ORCHESTRATION,
    ) -> RunRecord:
        """Create a new run record. Nothing is written until :meth:`complete_run`."""
        return RunRecord(
            run_id=str(uuid.uuid4())[:8],
            orchestration_name=orchestration_name,
            started_at=time.time(),
            input_data=input_data,
            kind=kind,
        )

    def add_event(self, record: RunRecord, event: ExecutionEvent) -> None:
        """Append an event to a run record."""
        record.events.append(event.to_dict())

    def complete_run(
        self,
        record: RunRecord,
        success: bool = True,
        output_data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark a run as completed and write its one record."""
        record.completed_at = time.time()
        record.success = success
        record.output_data = output_data
        record.error = error
        self._save(record)

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Load a run record by ID."""
        path = self._dir / f"{run_id}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding=_ENCODING))
        return RunRecord(**data)

    def _records(self) -> list[dict[str, Any]]:
        """Every readable record, newest first. A half-written file is skipped."""
        if not self._dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding=_ENCODING))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                records.append(data)
        records.sort(key=lambda data: float(data.get(_STARTED_AT) or 0), reverse=True)
        return records

    def count(self) -> int:
        """How many records there are."""
        return len(self._records())

    def list_runs(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Summaries of recent runs, newest first, skipping the first ``offset``."""
        return [
            {
                "run_id": data.get("run_id"),
                "orchestration_name": data.get("orchestration_name"),
                "kind": data.get("kind", _ORCHESTRATION),
                "started_at": data.get(_STARTED_AT),
                "success": data.get("success"),
                "event_count": len(data.get("events") or []),
            }
            for data in self._records()[offset : offset + limit]
        ]

    def _save(self, record: RunRecord) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{record.run_id}.json"
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(record.to_dict(), indent=2, default=str), encoding=_ENCODING)
        os.replace(partial, path)
