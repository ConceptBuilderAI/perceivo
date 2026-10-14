"""Coverage tests for orchestration execution and invoke routing."""

from unittest.mock import AsyncMock

import pytest

from perceivo.config import load
from perceivo.local.context import LocalExtensionManager
from perceivo.orchestration._builder import Orchestration
from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner._ports import invoke_port, invoke_port_parallel
from perceivo.runner.compiler import compile_project
from perceivo.runner.executor import _OrchestrationExecutor, run_component, run_orchestration
from perceivo.runner.loader import load_component_class
from perceivo.runner.types import NodeShell, PortConnection

from .fixtures_runner_coverage import (
    _AGENT,
    _AGENT_SETUP_FAIL,
    _AGENT_SETUP_OK,
    _AGENT_WITH_ARTIFACTS,
    _BAD_IMPORT,
    _END,
    _FAILING_AGENT,
    _FSM,
    _NO_ENTRY,
    _POLICY_NO_ARTIFACT,
    _START,
    _START_NO_ARTIFACT,
    _write_manifests,
)

# ---------------------------------------------------------------------------
# executor: run_component setup + load-failure paths
# ---------------------------------------------------------------------------


async def test_run_component_calls_setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "a.py"
    script.write_text(_AGENT_SETUP_OK, encoding="utf-8")
    result = await run_component(str(script), component_type="AGENT")
    assert result.success is True
    assert result.artifact.data == "ok"


async def test_run_component_load_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "bad.py"
    script.write_text(_NO_ENTRY, encoding="utf-8")
    result = await run_component(str(script), component_type="AGENT")
    assert result.success is False
    assert "ENTRY_CLASS" in result.error


# ---------------------------------------------------------------------------
# executor: orchestration walk branches (via run_orchestration end-to-end)
# ---------------------------------------------------------------------------


def _build_linear(workspace, *, start=_START, agent=_AGENT, end=_END, agent_has_script=True):
    orch = Orchestration("exec")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    a = orch.add_node(
        "a",
        "AGENT",
        script="a.py" if agent_has_script else None,
        manifest="a.yaml",
        node_id="a",
    )
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, a)
    orch.add_edge(a, e)
    orch.save(str(workspace))
    (workspace / "s.py").write_text(start, encoding="utf-8")
    if agent_has_script:
        (workspace / "a.py").write_text(agent, encoding="utf-8")
    (workspace / "e.py").write_text(end, encoding="utf-8")
    _write_manifests(workspace, ("s", "a", "e"))
    return workspace


# Records whether it was given an artifact.
_AGENT_SEES_NONE = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=None, response_data={"given_none": inp.artifact is None})
ENTRY_CLASS = "AgentC"
"""

_END_PASSES = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data=dict(inp.response_data))
ENTRY_CLASS = "EndC"
"""


async def test_run_orchestration_start_without_artifact(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, start=_START_NO_ARTIFACT, agent=_AGENT_SEES_NONE, end=_END_PASSES)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True
    # START returned no artifact, so the next node is given none, as on the engine.
    assert result.response_data["given_none"] is True


# Returns empty response data.
_AGENT_EMPTY_RESPONSE = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=inp.artifact, response_data={})
ENTRY_CLASS = "AgentC"
"""


async def test_a_node_returning_empty_response_data_hands_on_the_previous(
    tmp_workspace, monkeypatch
):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, agent=_AGENT_EMPTY_RESPONSE, end=_END_PASSES)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True
    # The engine replaces response data only with a non-empty value, so END is given
    # START's.
    assert result.response_data == {"started": True}


_START_NONE_RESPONSE = """
class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=None, response_data=None)
ENTRY_CLASS = "StartC"
"""

_AGENT_RECORDS_REQUEST = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=None, response_data={"saw": inp.request_data})
ENTRY_CLASS = "AgentC"
"""


async def test_a_start_returning_no_response_data_gives_the_next_node_empty(
    tmp_workspace, monkeypatch
):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(
        tmp_workspace, start=_START_NONE_RESPONSE, agent=_AGENT_RECORDS_REQUEST, end=_END_PASSES
    )
    result = await run_orchestration(str(tmp_workspace), input_data={"q": "hi"})
    assert result.success is True, result.error
    assert result.response_data == {"saw": {}}


_END_NONE_RESPONSE = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data=None)
ENTRY_CLASS = "EndC"
"""


async def test_an_end_returning_no_response_data_leaves_the_run_result_empty(
    tmp_workspace, monkeypatch
):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, end=_END_NONE_RESPONSE)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True, result.error
    assert result.response_data == {}


# END carries its input artifacts on; the run ends holding what END returns.
_END_KEEPS_ARTIFACTS = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data={"done": True},
                         artifacts=dict(inp.artifacts))
ENTRY_CLASS = "EndC"
"""


async def test_run_orchestration_agent_returns_named_artifacts(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, agent=_AGENT_WITH_ARTIFACTS, end=_END_KEEPS_ARTIFACTS)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True
    assert "extra" in result.artifacts


async def test_run_orchestration_node_without_script_passthrough(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, agent_has_script=False)
    result = await run_orchestration(str(tmp_workspace))
    # Node "a" has no script -> executor passes the artifact through unchanged.
    assert result.success is True
    assert result.artifact.data == "start"


async def test_run_orchestration_node_setup_failure_is_tolerated(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, agent=_AGENT_SETUP_FAIL)
    # Setup raises but is caught/logged; the run still completes.
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True


async def test_run_orchestration_component_failure_propagates(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, agent=_FAILING_AGENT)
    # Component raises -> _execute_node raises RuntimeError -> caught by
    # run_orchestration's top-level handler -> failure result.
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is False
    assert "agent-boom" in result.error


async def test_run_orchestration_load_failure(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_linear(tmp_workspace, agent=_BAD_IMPORT)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is False
    assert "Failed to load" in result.error


async def test_run_orchestration_policy_in_mainflow_no_successor(tmp_workspace, monkeypatch):
    """A POLICY node in the mainflow has no auto-successor -> walk break branch."""
    monkeypatch.chdir(tmp_workspace)
    _write_raw_policy_mainflow(tmp_workspace)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True


def _write_raw_policy_mainflow(workspace):
    (workspace / "s.py").write_text(_START_NO_ARTIFACT, encoding="utf-8")
    (workspace / "p.py").write_text(_POLICY_NO_ARTIFACT, encoding="utf-8")
    (workspace / "e.py").write_text(_END, encoding="utf-8")
    _write_manifests(workspace, ("s", "p", "e"))
    (workspace / "orchestration.yaml").write_text(
        """
name: policy_flow
nodes:
  - {id: s, component_type: START, name: s, script: s.py, manifest: s.yaml}
  - {id: p, component_type: POLICY, name: p, script: p.py, manifest: p.yaml}
  - {id: e, component_type: END, name: e, script: e.py, manifest: e.yaml}
ports:
  - {id: ps, node_id: s, name: out}
  - {id: pp, node_id: p, name: in}
  - {id: pe, node_id: e, name: in}
edges:
  - {source_port_id: ps, target_port_id: pp, kind: MAINFLOW}
""",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# port invocation: _ports.invoke_port / invoke_port_parallel
#
# These moved out of _OrchestrationExecutor into perceivo.runner._ports (the
# executor binds them with functools.partial). They take the executor as the
# first argument, so the coverage they give the executor's plan/ContextVar
# handling is unchanged -- only the call spelling moved.
# ---------------------------------------------------------------------------


def _build_invoke_executor(workspace):
    """START -> FSM -> END with FSM invoking a POLICY node."""
    orch = Orchestration("inv")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    f = orch.add_node("f", "FSM", script="f.py", manifest="f.yaml", node_id="f")
    p = orch.add_node("p", "POLICY", script="p.py", manifest="p.yaml", node_id="p")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, f)
    orch.add_edge(f, e)
    orch.add_edge(f, p, source_port="policy")
    orch.save(str(workspace))
    (workspace / "s.py").write_text(_START, encoding="utf-8")
    (workspace / "f.py").write_text(_FSM, encoding="utf-8")
    (workspace / "p.py").write_text(_POLICY_NO_ARTIFACT, encoding="utf-8")
    (workspace / "e.py").write_text(_END, encoding="utf-8")
    _write_manifests(workspace, ("s", "f", "p", "e"))

    plan = compile_project(str(workspace))
    ext = LocalExtensionManager(load(None))
    ex = _OrchestrationExecutor(plan, ext)
    for nid, shell in plan.shells_by_id.items():
        if shell.script_path:
            ex._component_instances[nid] = load_component_class(shell.script_path)
    return ex


async def test_invoke_port_executes_target(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    result = await invoke_port(
        ex,
        "policy",
        request_data={"q": 1},
        artifact=Artifact(type=ArtifactType.TEXT, data="hi"),
    )
    assert result["response_data"] == {"policy": True}


async def test_invoke_port_is_scoped_to_active_component(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    other = NodeShell(node_id="other", name="other", component_type="AGENT")
    other.invoke_connections["policy"] = [PortConnection("other_target", "port", "POLICY")]
    ex._plan.shells_by_id["other"] = other
    # _execute_node returns response_data; invoke_port shapes it to the platform
    # invoke contract (SPEC-4), which spreads response_data at the top level.
    ex._execute_node = AsyncMock(
        side_effect=lambda node_id, *_: {"response_data": {"target": node_id}}
    )

    token = ex._active_node_id.set("f")
    try:
        result = await invoke_port(ex, "policy")
    finally:
        ex._active_node_id.reset(token)

    # Routed to node "f"'s policy target ("p"), NOT the same-named port on "other".
    assert result["target"] == "p"
    assert result["response_data"] == {"target": "p"}


async def test_invoke_port_rejects_ambiguous_call_without_active_component(
    tmp_workspace, monkeypatch
):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    other = NodeShell(node_id="other", name="other", component_type="AGENT")
    other.invoke_connections["policy"] = [PortConnection("other_target", "port", "POLICY")]
    ex._plan.shells_by_id["other"] = other

    with pytest.raises(RuntimeError, match="ambiguous"):
        await invoke_port(ex, "policy")


async def test_invoke_port_unknown_port_raises(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    with pytest.raises(ValueError, match="No connection found for port"):
        await invoke_port(ex, "does_not_exist")


async def test_invoke_port_parallel_success(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    results = await invoke_port_parallel(ex, ["policy"])
    assert results["policy"]["response_data"] == {"policy": True}


async def test_invoke_port_parallel_error_captured(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    # Swallowing branch errors is the explicit "ignore_failures"
    # opt-out (no strategy is fail-closed "wait_all" and would raise).
    results = await invoke_port_parallel(ex, ["missing"], failure_strategy="ignore_failures")
    assert "error" in results["missing"]
    assert results["missing"]["artifact"] is None


async def test_invoke_port_parallel_fail_fast_raises(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    ex = _build_invoke_executor(tmp_workspace)
    with pytest.raises(ValueError, match="No connection found for port"):
        await invoke_port_parallel(ex, ["missing"], failure_strategy="fail_fast")
