"""Named artifacts across a nested ORCHESTRATION node, as the platform scopes them.

``subprocess`` gives the child its own artifacts, seeded with ``"main"``; ``inline``
splices the body into the parent's mainflow, so it reads the parent's.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from .test_runner_nested_modes import _END, _START, _edge, _node, _ports, _run, _write

# Emits two named artifacts upstream of a nested node.
_PRODUCER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        docs = {n: Artifact(type=ArtifactType.TEXT, data=n) for n in ("doc_a", "doc_b")}
        return AgentOutput(artifact=docs["doc_a"], response_data=dict(inp.request_data or {}),
                           artifacts=docs)
ENTRY_CLASS = "C"
"""


def _recorder(method: str, output: str, key: str) -> str:
    """A node that records the artifacts its input carries under ``key``."""
    return f"""
class C:
    async def {method}(self, inp):
        from perceivo.primitives import {output}
        data = dict(inp.request_data or {{}})
        data["{key}"] = {{k: v.data for k, v in inp.artifacts.items()}}
        return {output}(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""


def _recording_parent(root: Path, mode: str) -> Path:
    """START -> make -> n -(config)-> END; n nests START -> a -(config)-> END."""
    child = {
        "nodes": [
            _node("s", "START"),
            _node("a", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "a", "e"),
        "edges": [_edge("s", "a")],
    }
    _write(
        root / "nested" / "child",
        child,
        {
            "s": _recorder("start", "StartOutput", "start_saw"),
            "a": _recorder("run", "AgentOutput", "saw"),
            "e": _END,
        },
    )
    graph = {
        "nodes": [
            _node("s", "START"),
            _node("make", "AGENT"),
            _node(
                "n",
                "ORCHESTRATION",
                project="nested/child",
                config={"execution_mode": mode, "mainflow_target_node_ref": "e"},
            ),
            _node("e", "END"),
        ],
        "ports": _ports("s", "make", "n", "e"),
        "edges": [_edge("s", "make"), _edge("make", "n")],
    }
    return _write(root, graph, {"s": _START, "make": _PRODUCER, "e": _END})


async def test_subprocess_child_artifacts_are_seeded_with_main(tmp_path, monkeypatch):
    """A subprocess child starts with none of the parent's artifacts, only ``"main"``."""
    result = await _run(_recording_parent(tmp_path, "subprocess"), monkeypatch)

    assert result.success, result.error
    # The child's START sees only the nested node's input artifact, as main.
    assert result.response_data["start_saw"] == {"main": "doc_a"}


async def test_an_inline_body_reads_the_parents_artifacts(tmp_path, monkeypatch):
    """Inline bodies run in the parent's graph, so they read the parent's artifacts
    and no ``"main"`` is seeded for them."""
    result = await _run(_recording_parent(tmp_path, "inline"), monkeypatch)

    assert result.success, result.error
    assert "start_saw" not in result.response_data
    assert result.response_data["saw"] == {"doc_a": "doc_a", "doc_b": "doc_b"}


# Returns no artifact, clearing the one carried on.
_CLEARER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=None, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""

# Records the artifact and the artifact names it was given.
_AFTER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        data = dict(inp.request_data or {})
        data["after_artifact"] = None if inp.artifact is None else inp.artifact.data
        data["after_artifacts"] = sorted(inp.artifacts)
        return AgentOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""


async def test_an_inline_body_that_clears_the_artifact_leaves_the_parent_none(
    tmp_path, monkeypatch
):
    """The spliced body's last node returns no artifact, so the parent's next node
    is given none and the run holds no artifacts, as on the engine."""
    child = {
        "nodes": [
            _node("s", "START"),
            _node("a", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "a", "e"),
        "edges": [_edge("s", "a")],
    }
    _write(tmp_path / "nested" / "child", child, {"s": _START, "a": _CLEARER, "e": _END})
    graph = {
        "nodes": [
            _node("s", "START"),
            _node("make", "AGENT"),
            _node(
                "n",
                "ORCHESTRATION",
                project="nested/child",
                config={"execution_mode": "inline"},
            ),
            _node("after", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "make", "n", "after", "e"),
        "edges": [_edge("s", "make"), _edge("make", "n"), _edge("n", "after")],
    }
    root = _write(tmp_path, graph, {"s": _START, "make": _PRODUCER, "after": _AFTER, "e": _END})
    result = await _run(root, monkeypatch)

    assert result.success, result.error
    assert result.response_data["after_artifact"] is None
    assert result.response_data["after_artifacts"] == []


# A child START that returns no artifact.
_START_RETURNING_NONE = """
class C:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=None, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""


def _subprocess_parent(root: Path, scripts: dict[str, str], parent_nodes: list[str]) -> Path:
    """START -> parent_nodes... -> n -> END; n nests START -> a -> END as a subprocess."""
    child = {
        "nodes": [
            _node("s", "START"),
            _node("a", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "a", "e"),
        "edges": [_edge("s", "a")],
    }
    _write(root / "nested" / "child", child, {"e": _END, **scripts})
    chain = ["s", *parent_nodes, "n"]
    graph = {
        "nodes": [
            _node("s", "START"),
            *(_node(n, "AGENT") for n in parent_nodes),
            _node(
                "n",
                "ORCHESTRATION",
                project="nested/child",
                config={"execution_mode": "subprocess", "mainflow_target_node_ref": "e"},
            ),
            _node("e", "END"),
        ],
        "ports": _ports(*chain, "e"),
        "edges": [_edge(a, b) for a, b in pairwise(chain)],
    }
    parent_scripts = {"s": _START, "make": _PRODUCER, "clear": _CLEARER, "e": _END}
    return _write(root, graph, {n: parent_scripts[n] for n in ("s", *parent_nodes, "e")})


async def test_a_subprocess_start_returning_no_artifact_keeps_the_one_it_was_given(
    tmp_path, monkeypatch
):
    root = _subprocess_parent(
        tmp_path,
        {"s": _START_RETURNING_NONE, "a": _AFTER},
        ["make"],
    )
    result = await _run(root, monkeypatch)

    assert result.success, result.error
    assert result.response_data["after_artifact"] == "doc_a"
    assert result.response_data["after_artifacts"] == ["main"]


async def test_a_subprocess_given_no_artifact_starts_with_no_artifacts(tmp_path, monkeypatch):
    root = _subprocess_parent(
        tmp_path,
        {"s": _recorder("start", "StartOutput", "start_saw"), "a": _AFTER},
        ["make", "clear"],
    )
    result = await _run(root, monkeypatch)

    assert result.success, result.error
    assert result.response_data["start_saw"] == {}


# Returns its own artifact, "X", in place of the one it was given.
_MAKES_X = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(
            artifact=Artifact(type=ArtifactType.TEXT, data="X"),
            response_data=dict(inp.request_data or {}),
        )
ENTRY_CLASS = "C"
"""

# An END that returns no artifact.
_END_RETURNING_NONE = """
class C:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=None, response_data=dict(inp.response_data or {}))
ENTRY_CLASS = "C"
"""


async def test_a_subprocess_end_returning_no_artifact_hands_up_the_one_start_left(
    tmp_path, monkeypatch
):
    """The child's agent replaces the artifact, but its END returns none, so the
    nested node hands up the artifact the child held after START, not the agent's."""
    root = _subprocess_parent(
        tmp_path,
        {"s": _START, "a": _MAKES_X, "e": _END_RETURNING_NONE},
        ["make"],
    )
    result = await _run(root, monkeypatch)

    assert result.success, result.error
    assert result.artifact.data == "doc_a"
