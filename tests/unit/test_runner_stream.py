"""Unit tests for perceivo.runner.stream (event-streaming orchestration run)."""

from __future__ import annotations

from perceivo.orchestration._builder import Orchestration
from perceivo.runner.stream import run_stream

COMPLETE = "complete"

_START = """
class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=inp.artifact, response_data={})
ENTRY_CLASS = "StartC"
"""

_AGENT = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="x"))
ENTRY_CLASS = "AgentC"
"""

_END = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data={})
ENTRY_CLASS = "EndC"
"""


def _build(workspace):
    orch = Orchestration("strm")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml", node_id="a")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, a)
    orch.add_edge(a, e)
    orch.save(str(workspace))
    (workspace / "s.py").write_text(_START, encoding="utf-8")
    (workspace / "a.py").write_text(_AGENT, encoding="utf-8")
    (workspace / "e.py").write_text(_END, encoding="utf-8")
    for n in ("s", "a", "e"):
        (workspace / f"{n}.yaml").write_text("ports: []\n", encoding="utf-8")


async def _collect(agen):
    return [ev async for ev in agen]


def _types(events):
    return [e.event_type.value for e in events]


async def test_run_stream_missing_orchestration_yaml(tmp_path):
    events = await _collect(run_stream(str(tmp_path)))
    kinds = [event.event_type.value for event in events]
    assert kinds == ["log", "error", COMPLETE]
    assert "orchestration.yaml" in events[1].message
    assert events[2].data["success"] is False


async def test_run_stream_emits_node_and_complete_events(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build(tmp_workspace)
    events = await _collect(run_stream(str(tmp_workspace)))
    types = _types(events)
    assert "node_enter" in types
    assert "node_exit" in types
    assert COMPLETE in types
    # The terminal complete event should report success in its data payload.
    complete = [e for e in events if e.event_type.value == COMPLETE]
    assert complete[-1].data["success"] is True


async def test_run_stream_handles_execution_error(tmp_workspace, monkeypatch):
    monkeypatch.chdir(tmp_workspace)
    _build(tmp_workspace)

    async def boom(*args, **kwargs):
        raise RuntimeError("stream failure")

    import perceivo.runner as runner_pkg

    monkeypatch.setattr(runner_pkg, "run_orchestration", boom)
    events = await _collect(run_stream(str(tmp_workspace)))
    assert "error" in _types(events)
