"""Unit tests for perceivo.runner.executor (in-process orchestration runner).

Builds tiny real orchestration project dirs on disk and runs them end-to-end
through the local executor.  No network/cloud services are touched.
"""

from __future__ import annotations

from perceivo.orchestration._builder import Orchestration
from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner.executor import (
    _execute_component,
    run_component,
    run_orchestration,
)

# Component script templates ------------------------------------------------

_START = """
import perceivo
class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput, Artifact, ArtifactType
        return StartOutput(artifact=Artifact(type=ArtifactType.TEXT, data="start"),
                           response_data={"started": True})
ENTRY_CLASS = "StartC"
"""

_AGENT = """
import perceivo
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        data = inp.artifact.data + "+agent"
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data=data),
                           response_data={"agent": True})
ENTRY_CLASS = "AgentC"
"""

_END = """
import perceivo
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput, Artifact, ArtifactType
        return EndOutput(artifact=inp.artifact, response_data={"done": True})
ENTRY_CLASS = "EndC"
"""

_FAILING_AGENT = """
class BadAgent:
    async def run(self, inp):
        raise ValueError("boom")
ENTRY_CLASS = "BadAgent"
"""

# An END that reports failure without raising.  This is not the same shape as
# _FAILING_AGENT: the run *completes* and keeps the output it produced, and only
# the verdict is negative.
_FAILING_END = """
import perceivo
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput, Artifact, ArtifactType
        return EndOutput(artifact=inp.artifact,
                         response_data={"done": True, "verdict": "no"},
                         failed=True,
                         failure_reason="policy rejected the result")
ENTRY_CLASS = "EndC"
"""


def _build_orch(workspace, agent_script=_AGENT, end_script=_END):
    orch = Orchestration("exec")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml", node_id="a")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, a)
    orch.add_edge(a, e)
    orch.save(str(workspace))
    (workspace / "s.py").write_text(_START, encoding="utf-8")
    (workspace / "a.py").write_text(agent_script, encoding="utf-8")
    (workspace / "e.py").write_text(end_script, encoding="utf-8")
    manifest_body = "ports: []\ninput_parameters: []\nrequired_secrets: []\n"
    for n in ("s", "a", "e"):
        (workspace / f"{n}.yaml").write_text(manifest_body, encoding="utf-8")
    return workspace


# ---------------------------------------------------------------------------
# _execute_component (pure dispatch)
# ---------------------------------------------------------------------------


class _Run:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput

        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="ok"))


async def test_execute_component_agent():
    res = await _execute_component(_Run(), "AGENT", Artifact(type=ArtifactType.NONE, data=None), {})
    assert res.success is True
    assert res.artifact.data == "ok"


async def test_execute_component_tool():
    class _T:
        async def run(self, inp):
            from perceivo.primitives import ToolOutput

            return ToolOutput(response_data={"x": 1})

    res = await _execute_component(_T(), "TOOL", Artifact(type=ArtifactType.NONE, data=None), {})
    assert res.response_data == {"x": 1}


async def test_execute_component_fsm_policy_adapter():
    class _C:
        async def run(self, inp):
            from perceivo.primitives import FsmOutput

            return FsmOutput(artifact=inp.artifact, response_data={"ran": True})

    for ctype in ("FSM", "POLICY", "ADAPTER"):
        res = await _execute_component(_C(), ctype, Artifact(type=ArtifactType.NONE, data=None), {})
        assert res.success is True
    gone = await _execute_component(_C(), "LAYER", Artifact(type=ArtifactType.NONE, data=None), {})
    assert gone.success is False


async def test_execute_component_unsupported_type():
    none_art = Artifact(type=ArtifactType.NONE, data=None)
    res = await _execute_component(_Run(), "WIDGET", none_art, {})
    assert res.success is False
    assert "Unsupported" in res.error


async def test_execute_component_catches_exception():
    class _Boom:
        async def run(self, inp):
            raise RuntimeError("kaboom")

    none_art = Artifact(type=ArtifactType.NONE, data=None)
    res = await _execute_component(_Boom(), "AGENT", none_art, {})
    assert res.success is False
    assert "kaboom" in res.error


class _EndFailed:
    async def end(self, inp):
        from perceivo.primitives import EndOutput

        return EndOutput(
            artifact=inp.artifact,
            response_data={"kept": True},
            failed=True,
            failure_reason="policy rejected the result",
        )


async def test_execute_component_end_reports_terminal_failure():
    art = Artifact(type=ArtifactType.TEXT, data="final")
    res = await _execute_component(_EndFailed(), "END", art, {})
    # The component itself did not break, so dispatch still succeeded and the
    # output survives.  Only the verdict is negative.
    assert res.success is True
    assert res.artifact.data == "final"
    assert res.response_data == {"kept": True}
    assert res.terminal_failed is True
    assert res.terminal_failure_reason == "policy rejected the result"


async def test_execute_component_end_default_is_not_terminal_failure():
    class _EndOk:
        async def end(self, inp):
            from perceivo.primitives import EndOutput

            return EndOutput(artifact=inp.artifact, response_data={"done": True})

    art = Artifact(type=ArtifactType.TEXT, data="final")
    res = await _execute_component(_EndOk(), "END", art, {})
    assert res.success is True
    assert res.terminal_failed is False
    assert res.terminal_failure_reason is None


# ---------------------------------------------------------------------------
# run_orchestration (end-to-end, local)
# ---------------------------------------------------------------------------


async def test_run_orchestration_success(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True
    assert result.artifact is not None
    assert "agent" in str(result.artifact.data)


async def test_run_orchestration_missing_dir_returns_failure(tmp_path):
    result = await run_orchestration(str(tmp_path / "nope"))
    assert result.success is False
    assert result.error


async def test_run_orchestration_component_failure(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, agent_script=_FAILING_AGENT)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is False
    assert "boom" in result.error


async def test_run_orchestration_end_terminal_failure(tmp_workspace, monkeypatch):
    """An END that reports failure fails the run *and* keeps its output.

    The platform draws this distinction -- ``record_terminal_failure`` in
    the platform orchestration service persists the run as FAILED while retaining
    what END returned -- so the local runner has to draw it too.  Reporting
    success here would make every raise -> ``EndOutput(failed=True)`` migration
    green on the local bench and FAILED in production.
    """
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, end_script=_FAILING_END)
    result = await run_orchestration(str(tmp_workspace))

    assert result.success is False
    assert result.terminal_failed is True
    assert result.error == "policy rejected the result"
    assert result.terminal_failure_reason == "policy rejected the result"
    # Retained, not raised away -- this is what separates a terminal failure
    # from a component that blew up.
    assert result.artifact is not None
    assert "agent" in str(result.artifact.data)
    assert result.response_data == {"done": True, "verdict": "no"}
    # And the trace says so, rather than recording a green END.
    end_trace = [t for t in result.trace if t.component_type == "END"]
    assert [t.status for t in end_trace] == ["failed"]
    assert end_trace[0].error == "policy rejected the result"


async def test_run_orchestration_success_is_not_terminal_failed(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True
    assert result.terminal_failed is False
    assert result.terminal_failure_reason is None


# ---------------------------------------------------------------------------
# run_component (single component)
# ---------------------------------------------------------------------------


async def test_run_component_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "a.py"
    script.write_text(_AGENT, encoding="utf-8")
    result = await run_component(
        str(script),
        component_type="AGENT",
        input_artifact=Artifact(type=ArtifactType.TEXT, data="hi"),
    )
    assert result.success is True
    assert result.artifact.data == "hi+agent"


async def test_run_component_with_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "a.py"
    script.write_text(_AGENT, encoding="utf-8")
    manifest = tmp_path / "a.yaml"
    manifest.write_text(
        "input_parameters:\n  - name: thr\n    default: 5\nrequired_secrets:\n  - name: tok\n",
        encoding="utf-8",
    )
    result = await run_component(
        str(script),
        component_type="AGENT",
        manifest_path=str(manifest),
        input_artifact=Artifact(type=ArtifactType.TEXT, data="seed"),
    )
    assert result.success is True
    assert result.artifact.data == "seed+agent"


async def test_run_component_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "bad.py"
    script.write_text(_FAILING_AGENT, encoding="utf-8")
    result = await run_component(str(script), component_type="AGENT")
    assert result.success is False
    assert "boom" in result.error
