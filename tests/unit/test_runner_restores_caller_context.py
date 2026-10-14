"""A local orchestration run pops exactly the context it pushed, leaving the caller's in place."""

from __future__ import annotations

import contextlib

from perceivo._internal.context import _ExecutionContext
from perceivo.runner.executor import _OrchestrationExecutor, run_orchestration

from .test_runner_end_failure import _END_OK, _build_orch


async def test_the_callers_context_survives_a_run(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_OK)
    caller = object()
    _ExecutionContext.clear_all()
    _ExecutionContext.set_ext(caller)
    try:
        result = await run_orchestration(str(tmp_workspace))
        assert result.success, result.error
        assert _ExecutionContext.get_ext() is caller
    finally:
        _ExecutionContext.clear_all()


async def test_the_callers_context_survives_a_run_that_raises(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_OK)

    async def _raise(self, input_data, input_artifact):
        raise RuntimeError("boom")

    monkeypatch.setattr(_OrchestrationExecutor, "_execute_active", _raise)
    caller = object()
    _ExecutionContext.clear_all()
    _ExecutionContext.set_ext(caller)
    try:
        with contextlib.suppress(RuntimeError):
            await run_orchestration(str(tmp_workspace))
        assert _ExecutionContext.depth() == 1
        assert _ExecutionContext.get_ext() is caller
    finally:
        _ExecutionContext.clear_all()
