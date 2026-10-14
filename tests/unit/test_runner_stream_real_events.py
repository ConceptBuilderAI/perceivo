"""``run_stream`` yields the executor's own events, as nodes actually run.

The events used to be fabricated: one ``node_enter``/``node_exit`` pair per yaml
node, in yaml order, with a duration of about zero, all emitted **before** the
orchestration ran. Each test here fails against that shape.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from perceivo.runner.stream import run_stream

ENTER = "node_enter"
EXIT = "node_exit"
COMPLETE = "complete"
ERROR = "error"
START = "START"

_START = """
class C:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=inp.artifact, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""

_END = """
class C:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data=dict(inp.response_data or {}))
ENTRY_CLASS = "C"
"""

_SLOW = """
import asyncio
from pathlib import Path
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        await asyncio.sleep(0.2)
        (Path(__file__).parent / "slow.done").write_text("done")
        return AgentOutput(artifact=inp.artifact, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""

#: The durations test's own component. It waits on ``time.perf_counter`` -- the
#: clock the executor measures with -- rather than on the event loop's timer,
#: which on Windows has 15.625 ms resolution and can wake an ``asyncio.sleep``
#: up to one tick early. Blocking the loop is harmless here: this test needs no
#: other task to run while the node does (the queue-bridge test keeps ``_SLOW``).
_TIMED = """
import time
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        deadline = time.perf_counter() + 0.2
        while time.perf_counter() < deadline:
            time.sleep(0.005)
        return AgentOutput(artifact=inp.artifact, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""

_QUICK = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=inp.artifact, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""

_BROKEN = """
class C:
    async def run(self, inp):
        raise ValueError("broken node")
ENTRY_CLASS = "C"
"""

_INVOKER = """
import perceivo
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        await perceivo.flow.invoke("helper", {}, inp.artifact)
        return AgentOutput(artifact=inp.artifact, response_data={})
ENTRY_CLASS = "C"
"""

_TOOL = """
class C:
    async def run(self, inp):
        from perceivo.primitives import ToolOutput
        return ToolOutput(artifact=inp.artifact, response_data={})
ENTRY_CLASS = "C"
"""


def _graph(nodes: list[Any], ports: list[Any], edges: list[Any]) -> dict[str, Any]:
    return dict(zip(("nodes", "ports", "edges"), (nodes, ports, edges), strict=True))


def _node(nid: str, ctype: str, **extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"id": nid, "name": nid, "component_type": ctype}
    if ctype != "ORCHESTRATION":
        node["script"] = f"{nid}.py"
    node.update(extra)
    return node


def _ports(*ids: str) -> list[dict[str, Any]]:
    ports = []
    for nid in ids:
        ports.append(dict(id=f"{nid}_in", node_id=nid, name="input", port_type="in"))
        ports.append(dict(id=f"{nid}_out", node_id=nid, name="output", port_type="out"))
    return ports


def _edge(src: str, dst: str, kind: str = "MAINFLOW", port: str = "out") -> dict[str, Any]:
    return {"source_port_id": f"{src}_{port}", "target_port_id": f"{dst}_in", "kind": kind}


def _write(project: Path, graph: dict[str, Any], scripts: dict[str, str]) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    (project / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    for stem, body in scripts.items():
        (project / f"{stem}.py").write_text(body, encoding="utf-8")
    return project


def _chain(project: Path, middle: dict[str, str]) -> Path:
    """START -> the ``middle`` nodes in order -> END, with yaml node order REVERSED."""
    ids = ["s", *middle, "e"]
    nodes = [_node("s", START), *(_node(n, "AGENT") for n in middle), _node("e", "END")]
    graph = _graph(list(reversed(nodes)), _ports(*ids), [_edge(a, b) for a, b in pairwise(ids)])
    return _write(project, graph, {"s": _START, "e": _END, **middle})


async def _events(project: Path) -> list[Any]:
    return [event async for event in run_stream(str(project))]


def _node_events(events: list[Any]) -> list[tuple[str, str]]:
    return [
        (event.event_type.value, event.node_name)
        for event in events
        if event.event_type.value in (ENTER, EXIT)
    ]


async def test_events_follow_execution_order_not_yaml_order(tmp_path):
    project = _chain(tmp_path / "p", {"a": _QUICK, "b": _QUICK})
    names = [name for kind, name in _node_events(await _events(project)) if kind == ENTER]
    assert names == ["s", "a", "b", "e"]


async def test_durations_are_measured(tmp_path):
    project = _chain(tmp_path / "p", {"timed": _TIMED})
    exits = {e.node_name: e for e in await _events(project) if e.event_type.value == EXIT}
    assert exits["timed"].data["duration_ms"] >= 200


async def test_node_enter_reaches_the_consumer_while_the_node_runs(tmp_path):
    """Queue bridge: ``node_enter`` arrives before the node finishes, ``node_exit`` after."""
    project = _chain(tmp_path / "p", {"slow": _SLOW})
    marker = project / "slow.done"
    finished_at: dict[str, bool] = {}
    async for event in run_stream(str(project)):
        if event.node_name == "slow" and event.event_type.value in (ENTER, EXIT):
            finished_at[event.event_type.value] = marker.exists()
    assert finished_at == {ENTER: False, EXIT: True}


async def test_a_failing_node_reports_its_own_error_then_a_failed_complete(tmp_path):
    project = _chain(tmp_path / "p", {"bad": _BROKEN})
    events = await _events(project)
    tail = [(e.event_type.value, e.node_name) for e in events][-2:]
    assert tail == [(ERROR, "bad"), (COMPLETE, None)]
    assert events[-1].data["success"] is False


async def test_invoke_port_targets_emit_their_own_events(tmp_path):
    project = tmp_path / "p"
    helper_port = dict(id="a_helper", node_id="a", name="helper", port_type="tool")
    graph = _graph(
        [_node("s", START), _node("a", "AGENT"), _node("t", "TOOL"), _node("e", "END")],
        [*_ports("s", "a", "t", "e"), helper_port],
        [_edge("s", "a"), _edge("a", "e"), _edge("a", "t", "INVOKE", "helper")],
    )
    _write(project, graph, {"s": _START, "a": _INVOKER, "t": _TOOL, "e": _END})
    pairs = _node_events(await _events(project))
    assert pairs[pairs.index((ENTER, "a")) + 1 : pairs.index((EXIT, "a"))] == [
        (ENTER, "t"),
        (EXIT, "t"),
    ]


async def test_inline_nested_children_emit_their_own_events(tmp_path):
    root = tmp_path / "p"
    _chain(root / "nested" / "child", {"inner": _QUICK})
    graph = _graph(
        [
            _node("s", START),
            _node(
                "n", "ORCHESTRATION", project="nested/child", config={"execution_mode": "inline"}
            ),
            _node("e", "END"),
        ],
        _ports("s", "n", "e"),
        [_edge("s", "n"), _edge("n", "e")],
    )
    _write(root, graph, {"s": _START, "e": _END})
    names = [name for kind, name in _node_events(await _events(root)) if kind == ENTER]
    assert "inner" in names
