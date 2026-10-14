"""
perceivo.runner: lightweight local orchestration runner.

Execute orchestrations and individual components locally using
in-process async execution (no Ray, no cloud services required).

    from perceivo.runner import run_orchestration, run_component

    # Run a full orchestration
    result = await run_orchestration("./my-orchestration/", input_data={"query": "test"})

    # Run a single component
    result = await run_component("agent.py", component_type="AGENT", input_data={...})
"""

from typing import Any

from ..types.pagination import Page
from .executor import run_component, run_orchestration
from .history import RunHistory as _RunHistory
from .history import RunRecord
from .stream import run_stream

#: The runner's four verbs and the ``RunRecord`` type they return.
__all__ = [
    "RunRecord",
    "history",
    "run_component",
    "run_orchestration",
    "run_stream",
]


async def history(limit: int = 20, base_dir: str = ".") -> Page[dict[str, Any]]:
    """List recent local runs, newest first.

    Reads the record every run path writes under ``<home>/runs/``:
    ``run_orchestration``, ``Orchestration.run``, ``run_stream``,
    ``run_component`` and the CLI ``run`` commands. There is no platform call and no network; a
    project that has never run returns an empty page and no directory is created.

    Args:
        limit: Page size.
        base_dir: Where the home is resolved from, as a run started there
            resolves it: ``PERCEIVO_HOME`` if set; else the config file
            (the one ``PERCEIVO_CONFIG`` names, or the nearest
            ``perceivo.yaml`` at or above ``base_dir``) with the home beside
            it, at ``.perceivo`` or at the file's ``root:``; else
            ``.perceivo`` in the working directory, whatever ``base_dir`` is.
            A run is listed when the home it resolved is that same
            directory. A run given an explicit config file (``--config`` or
            ``config_path=``) resolves its home from that file unless
            ``PERCEIVO_HOME`` is set, so it is listed when the home that file
            gives (``.perceivo`` beside it, or its ``root:``) is the home
            ``base_dir`` resolves.

    Returns:
        A :class:`~perceivo.types.pagination.Page` of run summaries with
        ``run_id``, ``orchestration_name``, ``kind``, ``started_at``,
        ``success``, ``event_count``. ``total`` is the number of run records,
        and ``next()`` reaches the older ones.
    """
    runs = _RunHistory(base_dir=base_dir)
    total = runs.count()
    if limit < 1:
        # An empty page that cannot advance: a zero page size never reaches the end.
        return Page(data=[], page=1, page_size=0, total=total)

    async def fetch(page: int) -> Page[dict[str, Any]]:
        data = runs.list_runs(limit=limit, offset=(page - 1) * limit)
        return Page(data=data, page=page, page_size=limit, total=total, _fetch=fetch)

    return await fetch(1)
