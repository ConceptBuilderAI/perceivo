"""A failing END must fail the local run, in every entry point.

The platform reads ``EndOutput.failed`` / ``.failure_reason`` off the END node
and persists the run as FAILED while keeping the output END returned.  These
tests pin the local runner to
those same semantics, so an orchestration that fails in-cluster cannot pass
locally.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import perceivo
from perceivo.orchestration._builder import Orchestration
from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner.executor import run_component, run_orchestration
from perceivo.runner.stream import run_stream

_START = """
class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput, Artifact, ArtifactType
        return StartOutput(artifact=Artifact(type=ArtifactType.TEXT, data="start"),
                           response_data={"started": True})
ENTRY_CLASS = "StartC"
"""

_AGENT = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="agent"),
                           response_data={"agent": True})
ENTRY_CLASS = "AgentC"
"""

_END_OK = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data={"done": True})
ENTRY_CLASS = "EndC"
"""

_END_FAILED = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact,
                         response_data={"rejected_rows": 7},
                         failed=True,
                         failure_reason="7 rows failed validation")
ENTRY_CLASS = "EndC"
"""

_END_FAILED_NO_REASON = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data={}, failed=True)
ENTRY_CLASS = "EndC"
"""

# Mirrors the default the engine substitutes when an END declares failure
# without a reason.
_DEFAULT_REASON = "END component reported failure"


def _build_orch(workspace: Path, end_script: str) -> Path:
    orch = Orchestration("endfail")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml", node_id="a")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, a)
    orch.add_edge(a, e)
    orch.save(str(workspace))
    (workspace / "s.py").write_text(_START, encoding="utf-8")
    (workspace / "a.py").write_text(_AGENT, encoding="utf-8")
    (workspace / "e.py").write_text(end_script, encoding="utf-8")
    manifest_body = "ports: []\ninput_parameters: []\nrequired_secrets: []\n"
    for n in ("s", "a", "e"):
        (workspace / f"{n}.yaml").write_text(manifest_body, encoding="utf-8")
    return workspace


# ---------------------------------------------------------------------------
# run_orchestration
# ---------------------------------------------------------------------------


async def test_run_orchestration_failing_end_reports_failure(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_FAILED)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is False
    assert result.terminal_failed is True
    assert result.error == "7 rows failed validation"


async def test_run_orchestration_failing_end_retains_its_output(tmp_workspace, monkeypatch):
    """The platform persists a failed run's output_payload; so must the runner."""
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_FAILED)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is False
    assert result.response_data == {"rejected_rows": 7}
    assert result.artifact is not None
    assert result.artifact.data == "agent"


async def test_run_orchestration_failing_end_without_reason_uses_engine_default(
    tmp_workspace, monkeypatch
):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_FAILED_NO_REASON)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is False
    assert result.error == _DEFAULT_REASON


async def test_run_orchestration_failing_end_marks_the_end_node_failed(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_FAILED)
    result = await run_orchestration(str(tmp_workspace))
    end_trace = [t for t in result.trace if t.component_type == "END"]
    assert end_trace
    assert end_trace[-1].status == "failed"
    assert end_trace[-1].error == "7 rows failed validation"


async def test_run_orchestration_succeeding_end_still_passes(tmp_workspace, monkeypatch):
    """Regression guard: END failure handling must not turn passing runs red."""
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_OK)
    result = await run_orchestration(str(tmp_workspace))
    assert result.success is True
    assert result.terminal_failed is False
    assert result.error is None
    assert result.response_data == {"done": True}


# ---------------------------------------------------------------------------
# run_component
# ---------------------------------------------------------------------------


async def test_run_component_failing_end_reports_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "e.py"
    script.write_text(_END_FAILED, encoding="utf-8")
    result = await run_component(
        str(script),
        component_type="END",
        input_artifact=Artifact(type=ArtifactType.TEXT, data="seed"),
    )
    assert result.success is False
    assert result.terminal_failed is True
    assert result.error == "7 rows failed validation"
    assert result.response_data == {"rejected_rows": 7}


async def test_run_component_succeeding_end_still_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "e.py"
    script.write_text(_END_OK, encoding="utf-8")
    result = await run_component(
        str(script),
        component_type="END",
        input_artifact=Artifact(type=ArtifactType.TEXT, data="seed"),
    )
    assert result.success is True
    assert result.terminal_failed is False
    assert result.error is None


# ---------------------------------------------------------------------------
# run_stream
# ---------------------------------------------------------------------------


async def _collect(agen):
    return [ev async for ev in agen]


async def test_run_stream_failing_end_completes_unsuccessfully(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_FAILED)
    events = await _collect(run_stream(str(tmp_workspace)))
    complete = [e for e in events if e.event_type.value == "complete"]
    assert complete
    assert complete[-1].data["success"] is False
    assert "error" in [e.event_type.value for e in events]


async def test_run_stream_failing_end_records_failure_in_history(
    tmp_workspace, monkeypatch, isolated_perceivo_home
):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_FAILED)
    events = await _collect(run_stream(str(tmp_workspace)))
    run_id = [e for e in events if e.event_type.value == "complete"][-1].data["run_id"]
    record = json.loads(
        (isolated_perceivo_home / "runs" / f"{run_id}.json").read_text(encoding="utf-8")
    )
    assert record["success"] is False
    assert record["error"] == "7 rows failed validation"


async def test_run_stream_succeeding_end_still_completes_ok(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build_orch(tmp_workspace, _END_OK)
    events = await _collect(run_stream(str(tmp_workspace)))
    complete = [e for e in events if e.event_type.value == "complete"]
    assert complete
    assert complete[-1].data["success"] is True
    assert "error" not in [e.event_type.value for e in events]


# ---------------------------------------------------------------------------
# CLI process exit code
# ---------------------------------------------------------------------------


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Drive the CLI in a real subprocess so the process exit code is measured.

    ``PYTHONPATH`` is pinned to the package under test rather than trusting
    whatever ``perceivo`` distribution happens to be installed, so the exit code
    belongs to this checkout.
    """
    package_root = str(Path(perceivo.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", "from perceivo._cli import main; main()", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
    )


def test_cli_orchestration_exits_nonzero_when_end_fails(tmp_workspace):
    _build_orch(tmp_workspace, _END_FAILED)
    proc = _run_cli(tmp_workspace, "run", "orchestration", str(tmp_workspace), "--json")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["status"] == "FAIL"
    assert payload["error"] == "7 rows failed validation"


def test_cli_orchestration_exits_zero_when_end_succeeds(tmp_workspace):
    """Regression guard: a passing orchestration must still exit 0."""
    _build_orch(tmp_workspace, _END_OK)
    proc = _run_cli(tmp_workspace, "run", "orchestration", str(tmp_workspace), "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["status"] == "PASS"


def test_cli_component_exits_nonzero_when_end_fails(tmp_path):
    (tmp_path / "e.py").write_text(_END_FAILED, encoding="utf-8")
    proc = _run_cli(tmp_path, "run", "component", "e.py", "--type", "END", "--json")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["error"] == "7 rows failed validation"


def test_cli_component_exits_zero_when_end_succeeds(tmp_path):
    (tmp_path / "e.py").write_text(_END_OK, encoding="utf-8")
    proc = _run_cli(tmp_path, "run", "component", "e.py", "--type", "END", "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["success"] is True


# ---------------------------------------------------------------------------
# END reached through an invoke port
# ---------------------------------------------------------------------------

# AGENT -> END is a legal INVOKE edge (validator ALLOWED_CONNECTIONS), and the
# engine routes every END dispatch — mainflow or invoke — through the one
# handler that records the verdict. That handler OVERWRITES the verdict: the
# last END to run wins.
# This END declares failure on its first call only, so the later mainflow call
# returns clean and decides the run; an END that fails on its LAST call fails it.
_END_FAILS_FIRST_CALL_ONLY = """
_calls = []

class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        _calls.append(1)
        if len(_calls) == 1:
            return EndOutput(artifact=inp.artifact, response_data={},
                             failed=True, failure_reason="invoked END rejected the run")
        return EndOutput(artifact=inp.artifact, response_data={"done": True})
ENTRY_CLASS = "EndC"
"""

_AGENT_INVOKING_END = """
import perceivo

class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        await perceivo.flow.invoke("finish", {}, inp.artifact)
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="agent"))
ENTRY_CLASS = "AgentC"
"""

_INVOKE_END_ORCH = """
name: Invoke End
nodes:
- id: n_start
  name: START
  component_type: START
  script: s.py
  manifest: m.yaml
- id: n_a
  name: Agent
  component_type: AGENT
  script: a.py
  manifest: m.yaml
- id: n_end
  name: END
  component_type: END
  script: e.py
  manifest: m.yaml
ports:
- id: p_start_out
  node_id: n_start
  name: output
  port_type: out
- id: p_a_in
  node_id: n_a
  name: input
  port_type: in
- id: p_a_finish
  node_id: n_a
  name: finish
  port_type: tool
- id: p_end_in
  node_id: n_end
  name: input
  port_type: in
edges:
- source_port_id: p_start_out
  target_port_id: p_a_in
  kind: MAINFLOW
- source_port_id: p_a_in
  target_port_id: p_end_in
  kind: MAINFLOW
- source_port_id: p_a_finish
  target_port_id: p_end_in
  kind: INVOKE
"""


def _invoke_end_project(tmp_path, end_script: str) -> None:
    (tmp_path / "orchestration.yaml").write_text(_INVOKE_END_ORCH, encoding="utf-8")
    (tmp_path / "s.py").write_text(_START, encoding="utf-8")
    (tmp_path / "a.py").write_text(_AGENT_INVOKING_END, encoding="utf-8")
    (tmp_path / "e.py").write_text(end_script, encoding="utf-8")
    (tmp_path / "m.yaml").write_text(
        "ports: []\ninput_parameters: []\nrequired_secrets: []\n", encoding="utf-8"
    )


async def test_end_failure_through_invoke_port_is_overwritten_by_a_later_clean_end(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _invoke_end_project(tmp_path, _END_FAILS_FIRST_CALL_ONLY)

    result = await run_orchestration(str(tmp_path))
    assert result.success is True, result.error
    assert result.terminal_failed is False
    assert result.error is None
    assert [t.status for t in result.trace if t.node == "END"] == ["failed", "ok"]


_END_FAILS_SECOND_CALL_ONLY = _END_FAILS_FIRST_CALL_ONLY.replace(
    "len(_calls) == 1", "len(_calls) == 2"
).replace("invoked END rejected the run", "final END rejected the run")


async def test_end_failure_on_the_last_end_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke_end_project(tmp_path, _END_FAILS_SECOND_CALL_ONLY)

    result = await run_orchestration(str(tmp_path))
    assert result.success is False
    assert result.error == "final END rejected the run"
