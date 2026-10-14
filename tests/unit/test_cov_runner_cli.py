"""Coverage-focused tests for the runner (executor/compiler) and CLI commands.

These tests target uncovered branches in:
  - perceivo/runner/executor.py
  - perceivo/runner/compiler.py
  - perceivo/_cli/run_cmd.py
  - perceivo/_cli/publish_cmd.py
  - perceivo/_cli/validate_cmd.py

They build small real orchestration/component projects on disk (or raw
orchestration.yaml files for compiler edge cases) and drive them through the
public entry points. Network/publish is mocked; everything else is real.

The extension-configuration slice -- ``_configure_ext_from_manifest``,
``_configure_ext_from_shell`` and the ``load_manifest`` mapping check -- lives
in ``test_runner_ext_configuration.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from perceivo.config import load
from perceivo.local.context import LocalExtensionManager
from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner.executor import (
    _execute_component,
    _OrchestrationExecutor,
)
from perceivo.runner.types import CompileResult, NodeShell, PortConnection


@pytest.mark.asyncio
@pytest.mark.parametrize("should_fail", [False, True])
async def test_execute_node_restores_parent_config_after_invoke(should_fail):
    ext = LocalExtensionManager(load(None))
    ext.set_node_env(values={"owner": "parent"}, declarations={})
    ext.set_node_secret_info(declarations={}, aliases={"token": "parent-secret"})
    shell = NodeShell(
        node_id="target",
        name="target",
        component_type="AGENT",
        config={
            "input_parameters": {"values": {"owner": "child"}},
            "secret_bindings": {"bindings": {"token": "child-secret"}},
        },
    )
    plan = CompileResult(
        root_id="target",
        start_node_id="target",
        end_node_id="target",
        shells_by_id={"target": shell},
    )
    executor = _OrchestrationExecutor(plan, ext)

    class Component:
        async def run(self, _input):
            assert ext.get_node_env()["values"]["owner"] == "child"
            assert ext.get_node_secret_info()["aliases"]["token"] == "child-secret"
            if should_fail:
                raise RuntimeError("child failed")
            from perceivo.primitives import AgentOutput

            return AgentOutput(artifact=_NONE_ART, response_data={"ok": True})

    executor._component_instances["target"] = Component()

    if should_fail:
        with pytest.raises(RuntimeError, match="child failed"):
            await executor._execute_node("target", _NONE_ART, {})
    else:
        await executor._execute_node("target", _NONE_ART, {})

    assert ext.get_node_env()["values"] == {"owner": "parent"}
    assert ext.get_node_secret_info()["aliases"] == {"token": "parent-secret"}


@pytest.mark.asyncio
async def test_nested_executor_restores_parent_callbacks_and_context_on_exception():
    ext = LocalExtensionManager(load(None))

    async def parent_invoke(**kwargs):
        return {"owner": "parent"}

    async def parent_parallel(**kwargs):
        return {"branch": {"owner": "parent"}}

    ext.set_invoke_port_callback(parent_invoke)
    ext.set_invoke_port_parallel_callback(parent_parallel)
    ext.activate()
    plan = CompileResult(root_id="", start_node_id="", end_node_id="", shells_by_id={})
    child = _OrchestrationExecutor(plan, ext)
    child._execute_active = AsyncMock(side_effect=RuntimeError("nested failed"))
    try:
        with pytest.raises(RuntimeError, match="nested failed"):
            await child.execute({})
        assert await ext.invoke_port("tool") == {"owner": "parent"}
        assert await ext.invoke_port_parallel(["branch"]) == {"branch": {"owner": "parent"}}
    finally:
        ext.deactivate()


@pytest.mark.asyncio
async def test_local_context_keeps_parallel_node_config_and_callbacks_task_local():
    ext = LocalExtensionManager(load(None))

    async def worker(label):
        ext.set_node_env(values={"owner": label}, declarations={})
        ext.set_node_secret_info(declarations={}, aliases={"token": f"secret-{label}"})

        async def invoke_callback(**kwargs):
            return {"owner": label}

        async def parallel_callback(**kwargs):
            return {"branch": {"owner": label}}

        ext.set_invoke_port_callback(invoke_callback)
        ext.set_invoke_port_parallel_callback(parallel_callback)
        await asyncio.sleep(0)
        return (
            ext.get_node_env()["values"]["owner"],
            ext.get_node_secret_info()["aliases"]["token"],
            await ext.invoke_port("tool"),
            await ext.invoke_port_parallel(["branch"]),
        )

    first, second = await asyncio.gather(worker("first"), worker("second"))

    assert first == (
        "first",
        "secret-first",
        {"owner": "first"},
        {"branch": {"owner": "first"}},
    )
    assert second == (
        "second",
        "secret-second",
        {"owner": "second"},
        {"branch": {"owner": "second"}},
    )


@pytest.mark.asyncio
async def test_concurrent_nested_executors_keep_invoke_routing_task_local():
    ext = LocalExtensionManager(load(None))

    def child_executor(target):
        source = NodeShell(node_id="source", name="source", component_type="AGENT")
        source.invoke_connections["tool"] = [PortConnection(target, "in", "TOOL")]
        target_shell = NodeShell(node_id=target, name=target, component_type="TOOL")
        plan = CompileResult(
            root_id="source",
            start_node_id="source",
            end_node_id=target,
            shells_by_id={"source": source, target: target_shell},
        )
        executor = _OrchestrationExecutor(plan, ext)
        executor._execute_node = AsyncMock(
            side_effect=lambda node_id, *_: {"response_data": {"target": node_id}}
        )
        return executor

    async def invoke_from(executor):
        executor._activate_callbacks()
        token = executor._active_node_id.set("source")
        try:
            await asyncio.sleep(0)
            return await ext.invoke_port("tool")
        finally:
            executor._active_node_id.reset(token)

    left, right = await asyncio.gather(
        invoke_from(child_executor("left_target")),
        invoke_from(child_executor("right_target")),
    )

    assert left["response_data"]["target"] == "left_target"
    assert right["response_data"]["target"] == "right_target"


# ---------------------------------------------------------------------------
# executor: _execute_component dispatch for every component type
# ---------------------------------------------------------------------------

_NONE_ART = Artifact(type=ArtifactType.NONE, data=None)


async def test_execute_component_start_and_end():
    class _S:
        async def start(self, inp):
            from perceivo.primitives import Artifact as A
            from perceivo.primitives import ArtifactType as AT
            from perceivo.primitives import StartOutput

            return StartOutput(artifact=A(type=AT.TEXT, data="s"))

    class _E:
        async def end(self, inp):
            from perceivo.primitives import EndOutput

            return EndOutput(artifact=inp.artifact, response_data={"e": 1})

    r1 = await _execute_component(_S(), "START", _NONE_ART, {})
    assert (r1.success, r1.artifact.data) == (True, "s")
    r2 = await _execute_component(_E(), "END", _NONE_ART, {"a": 1})
    assert (r2.success, r2.response_data) == (True, {"e": 1})


async def test_execute_component_start_end_underscore_methods():
    class _S:
        async def _start(self, inp):
            from perceivo.primitives import StartOutput

            return StartOutput(response_data={"underscore": True})

    class _E:
        async def _end(self, inp):
            from perceivo.primitives import EndOutput

            return EndOutput(artifact=inp.artifact, response_data={"u": True})

    r1 = await _execute_component(_S(), "START", _NONE_ART, {})
    assert r1.response_data == {"underscore": True}
    r2 = await _execute_component(_E(), "END", _NONE_ART, {})
    assert r2.response_data == {"u": True}


async def test_execute_component_tool_layer_and_adapter():
    class _T:
        async def run(self, inp):
            from perceivo.primitives import ToolOutput

            return ToolOutput(response_data={"t": 1})

    class _Layer:
        async def run(self, inp):
            from perceivo.primitives import FsmOutput

            return FsmOutput(artifact=inp.artifact, response_data={"layer": True})

    class _Adapter:
        async def run(self, inp):
            from perceivo.primitives import AdapterOutput

            return AdapterOutput(response_data={"ad": 1})

    rt = await _execute_component(_T(), "TOOL", _NONE_ART, {})
    assert rt.response_data == {"t": 1}
    rl = await _execute_component(_Layer(), "FSM", _NONE_ART, {})
    assert (rl.success, rl.response_data) == (True, {"layer": True})
    # LAYER is not an alias of FSM: it is an unsupported type.
    gone = await _execute_component(_Layer(), "LAYER", _NONE_ART, {})
    assert gone.success is False
    assert "Unsupported component type" in gone.error
    ra = await _execute_component(_Adapter(), "ADAPTER", _NONE_ART, {})
    assert ra.response_data == {"ad": 1}


async def test_execute_component_policy():
    class _P:
        async def run(self, inp):
            from perceivo.primitives import PolicyOutput

            return PolicyOutput(next_state="go", response_data={"p": 1})

    rp = await _execute_component(_P(), "POLICY", _NONE_ART, {})
    assert (rp.success, rp.response_data) == (True, {"p": 1})


async def test_execute_component_unsupported_and_error():
    class _R:
        async def run(self, inp):
            from perceivo.primitives import AgentOutput, Artifact, ArtifactType

            return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="x"))

    bad = await _execute_component(_R(), "WIDGET", _NONE_ART, {})
    assert (bad.success, "Unsupported" in bad.error) == (False, True)

    class _Boom:
        async def run(self, inp):
            raise RuntimeError("kaboom")

    err = await _execute_component(_Boom(), "AGENT", _NONE_ART, {})
    assert (err.success, "kaboom" in err.error) == (False, True)
