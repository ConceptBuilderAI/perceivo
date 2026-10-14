"""Nested ORCHESTRATION nodes in the local runner: hops, modes and trace.

Mirrors the platform: ``inline`` strips the child's START/END and runs its single
entry..exit body in place; ``subprocess`` (the default) runs the whole child with
the upstream ``response_data`` as its request. A config
``mainflow_target_node_ref`` is a mainflow hop, as it is for the engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from perceivo.runner import _nested
from perceivo.runner.compiler import compile_orchestration
from perceivo.runner.executor import run_orchestration
from perceivo.types.errors import ConfigurationError

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

_FAILING_END = """
class C:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data={}, failed=True,
                         failure_reason="child said no")
ENTRY_CLASS = "C"
"""


def _agent(tag: str) -> str:
    return f"""
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        data = dict(inp.request_data or {{}})
        data["path"] = list(data.get("path", [])) + ["{tag}"]
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="{tag}"),
                           response_data=data)
ENTRY_CLASS = "C"
"""


def _write(project: Path, graph: dict[str, Any], scripts: dict[str, str]) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    (project / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    for stem, body in scripts.items():
        (project / f"{stem}.py").write_text(body, encoding="utf-8")
    return project


def _node(nid: str, ctype: str, **extra: Any) -> dict[str, Any]:
    node = {"id": nid, "name": nid, "component_type": ctype}
    if ctype != "ORCHESTRATION":
        node["script"] = f"{nid}.py"
    node.update(extra)
    return node


def _ports(*ids: str) -> list[dict[str, Any]]:
    out = []
    for nid in ids:
        out.append({"id": f"{nid}_in", "node_id": nid, "name": "input", "port_type": "in"})
        out.append({"id": f"{nid}_out", "node_id": nid, "name": "output", "port_type": "out"})
    return out


def _edge(src: str, dst: str) -> dict[str, Any]:
    return {"source_port_id": f"{src}_out", "target_port_id": f"{dst}_in", "kind": "MAINFLOW"}


def _leaf(project: Path, tag: str, end: str = _END) -> Path:
    """START -> a (edge) ; a -> END via config target only."""
    graph = {
        "nodes": [
            _node("s", "START"),
            _node("a", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "a", "e"),
        "edges": [_edge("s", "a")],
    }
    return _write(project, graph, {"s": _START, "a": _agent(tag), "e": end})


def _parent(root: Path, mode: str, *, second_end: str = _END) -> Path:
    """START -> n1 -(config)-> n2 -(config)-> END, both nested."""
    _leaf(root / "nested" / "one", "one")
    _leaf(root / "nested" / "two", "two", end=second_end)
    graph = {
        "nodes": [
            _node("s", "START"),
            _node(
                "n1",
                "ORCHESTRATION",
                project="nested/one",
                config={"execution_mode": mode, "mainflow_target_node_ref": "n2"},
            ),
            _node(
                "n2",
                "ORCHESTRATION",
                project="nested/two",
                config={"execution_mode": mode, "mainflow_target_node_ref": "e"},
            ),
            _node("e", "END"),
        ],
        "ports": _ports("s", "n1", "n2", "e"),
        "edges": [_edge("s", "n1")],
    }
    return _write(root, graph, {"s": _START, "e": _END})


async def _run(project: Path, monkeypatch) -> Any:
    monkeypatch.chdir(project)
    return await run_orchestration(str(project), input_data={"q": "hi"})


def _names(result: Any) -> list[tuple[str, str]]:
    return [(t.node, t.status) for t in result.trace]


# --------------------------------------------------------------------------- #
# config mainflow hop                                                          #
# --------------------------------------------------------------------------- #


def test_config_target_is_a_mainflow_hop():
    plan = compile_orchestration(
        {
            "nodes": [
                _node("s", "START"),
                _node("n", "ORCHESTRATION", config={"mainflow_target_node_ref": "x"}),
                _node("x", "AGENT"),
                _node("e", "END"),
            ],
            "ports": _ports("s", "n", "x", "e"),
            "edges": [_edge("s", "n"), _edge("x", "e")],
        }
    )
    assert plan.shells_by_id["n"].mainflow_successors == ["x"]


def test_config_target_resolves_a_bare_index_and_is_not_duplicated():
    plan = compile_orchestration(
        {
            "nodes": [
                _node("node_0", "START", config={"mainflow_target_node_index": 1}),
                _node("node_1", "AGENT", config="not-a-mapping"),
                _node("node_2", "END"),
            ],
            "ports": _ports("node_0", "node_1", "node_2"),
            "edges": [_edge("node_0", "node_1")],
        }
    )
    assert plan.shells_by_id["node_0"].mainflow_successors == ["node_1"]


def test_config_target_naming_no_node_raises():
    orchestration = {
        "nodes": [
            _node("s", "START", config={"mainflow_target_node_id": "ghost"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "e"),
        "edges": [],
    }
    with pytest.raises(ConfigurationError, match="mainflow target 'ghost'"):
        compile_orchestration(orchestration)


# --------------------------------------------------------------------------- #
# modes + trace                                                                #
# --------------------------------------------------------------------------- #


async def test_subprocess_series_runs_both_children_and_namespaces_their_trace(
    tmp_path, monkeypatch
):
    result = await _run(_parent(tmp_path, "subprocess"), monkeypatch)

    assert result.success, result.error
    assert result.response_data["path"] == ["one", "two"]
    assert _names(result) == [
        ("s", "ok"),
        ("n1:s", "ok"),
        ("n1:a", "ok"),
        ("n1:e", "ok"),
        ("n1", "ok"),
        ("n2:s", "ok"),
        ("n2:a", "ok"),
        ("n2:e", "ok"),
        ("n2", "ok"),
        ("e", "ok"),
    ]


async def test_inline_series_skips_child_start_and_end(tmp_path, monkeypatch):
    result = await _run(_parent(tmp_path, "inline"), monkeypatch)

    assert result.success, result.error
    assert result.response_data["path"] == ["one", "two"]
    assert _names(result) == [
        ("s", "ok"),
        ("n1:a", "ok"),
        ("n1", "ok"),
        ("n2:a", "ok"),
        ("n2", "ok"),
        ("e", "ok"),
    ]


async def test_inline_nesting_inside_inline_namespaces_every_level(tmp_path, monkeypatch):
    inner = tmp_path / "outer"
    _leaf(inner / "nested" / "leaf", "leaf")
    _write(
        inner,
        {
            "nodes": [
                _node("s", "START"),
                _node(
                    "mid",
                    "ORCHESTRATION",
                    project="nested/leaf",
                    config={"execution_mode": "inline", "mainflow_target_node_ref": "e"},
                ),
                _node("e", "END"),
            ],
            "ports": _ports("s", "mid", "e"),
            "edges": [_edge("s", "mid")],
        },
        {"s": _START, "e": _END},
    )
    root = tmp_path / "root"
    root.mkdir()
    (root / "nested").mkdir()
    import shutil

    shutil.copytree(inner, root / "nested" / "outer")
    _write(
        root,
        {
            "nodes": [
                _node("s", "START"),
                _node(
                    "top",
                    "ORCHESTRATION",
                    project="nested/outer",
                    config={"execution_mode": "inline", "mainflow_target_node_ref": "e"},
                ),
                _node("e", "END"),
            ],
            "ports": _ports("s", "top", "e"),
            "edges": [_edge("s", "top")],
        },
        {"s": _START, "e": _END},
    )

    result = await _run(root, monkeypatch)

    assert result.success, result.error
    assert ("top:mid:a", "ok") in _names(result)
    assert ("top:mid", "ok") in _names(result)


async def test_subprocess_child_end_failure_is_overwritten_by_the_parent_end(tmp_path, monkeypatch):
    """The engine shares one END verdict across parent and child; the last END wins.

    The engine neither saves nor restores the terminal verdict around the child,
    and every END overwrites it, so the parent's clean END decides the run.
    """
    result = await _run(_parent(tmp_path, "subprocess", second_end=_FAILING_END), monkeypatch)

    assert result.success, result.error
    assert result.terminal_failed is False
    assert ("n2:e", "failed") in _names(result)
    assert ("n2", "ok") in _names(result)
    assert _names(result)[-1] == ("e", "ok")


async def test_subprocess_child_end_verdict_is_handed_to_the_parent(tmp_path, monkeypatch):
    """With the parent's END skipped, the child's failed END is the last one to run."""
    project = _parent(tmp_path, "subprocess", second_end=_FAILING_END)
    (project / "e.py").unlink()  # parent END has no component: nothing overwrites the verdict

    result = await _run(project, monkeypatch)

    assert not result.success
    assert result.terminal_failed is True
    assert result.error == "child said no"
    assert ("n2", "ok") in _names(result)


async def test_parent_end_failure_after_a_clean_child_fails_the_run(tmp_path, monkeypatch):
    project = _parent(tmp_path, "subprocess")
    (project / "e.py").write_text(_FAILING_END, encoding="utf-8")

    result = await _run(project, monkeypatch)

    assert not result.success
    assert result.error == "child said no"
    assert ("n2:e", "ok") in _names(result)
    assert ("e", "failed") in _names(result)


def _inline_chain(root: Path, levels: int) -> Path:
    """``levels`` inline ORCHESTRATION nodes, each nesting the next, over a leaf."""
    _leaf(root / "leaf", "leaf")
    child = "leaf"
    for level in range(levels, 0, -1):
        name = f"l{level}"
        _write(
            root / name,
            {
                "nodes": [
                    _node("s", "START"),
                    _node(
                        "n",
                        "ORCHESTRATION",
                        project=f"../{child}",
                        config={"execution_mode": "inline", "mainflow_target_node_ref": "e"},
                    ),
                    _node("e", "END"),
                ],
                "ports": _ports("s", "n", "e"),
                "edges": [_edge("s", "n")],
            },
            {"s": _START, "e": _END},
        )
        child = name
    return root / "l1"


async def test_inline_nesting_at_the_engine_maximum_depth_runs(tmp_path, monkeypatch):
    # The run's root is l1; l1..l5 each hold one inline node, so the leaf sits
    # five inline levels down.
    result = await _run(_inline_chain(tmp_path, _nested.MAX_INLINE_DEPTH), monkeypatch)

    assert result.success, result.error
    assert ("n:n:n:n:n:a", "ok") in _names(result)


async def test_inline_nesting_past_the_engine_maximum_depth_fails(tmp_path, monkeypatch):
    result = await _run(_inline_chain(tmp_path, _nested.MAX_INLINE_DEPTH + 1), monkeypatch)

    assert not result.success
    assert "Inline orchestration nesting exceeds maximum depth 5" in result.error


def test_max_inline_depth_matches_the_engine():
    assert _nested.MAX_INLINE_DEPTH == 5


async def test_subprocess_resets_the_inline_depth(tmp_path, monkeypatch):
    """A subprocess child is compiled as its own bundle, flattened from depth 0."""
    chain = _inline_chain(tmp_path / "chain", _nested.MAX_INLINE_DEPTH)
    _write(
        tmp_path,
        {
            "nodes": [
                _node("s", "START"),
                _node(
                    "outer",
                    "ORCHESTRATION",
                    project="chain/l1",
                    config={"execution_mode": "inline", "mainflow_target_node_ref": "e"},
                ),
                _node("e", "END"),
            ],
            "ports": _ports("s", "outer", "e"),
            "edges": [_edge("s", "outer")],
        },
        {"s": _START, "e": _END},
    )
    assert chain.exists()
    too_deep = await _run(tmp_path, monkeypatch)
    assert not too_deep.success
    assert "maximum depth 5" in too_deep.error

    graph = yaml.safe_load((tmp_path / "orchestration.yaml").read_text(encoding="utf-8"))
    graph["nodes"][1]["config"]["execution_mode"] = "subprocess"
    (tmp_path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    result = await _run(tmp_path, monkeypatch)
    assert result.success, result.error


async def test_nested_node_without_a_project_is_traced_as_failed(tmp_path, monkeypatch):
    project = _write(
        tmp_path,
        {
            "nodes": [
                _node("s", "START"),
                _node("n", "ORCHESTRATION", config={"mainflow_target_node_ref": "e"}),
                _node("e", "END"),
            ],
            "ports": _ports("s", "n", "e"),
            "edges": [_edge("s", "n")],
        },
        {"s": _START, "e": _END},
    )
    result = await _run(project, monkeypatch)

    assert not result.success
    assert "has no valid project path" in result.error
    assert ("n", "failed") in _names(result)


async def test_inline_child_that_fails_to_load_raises(tmp_path, monkeypatch):
    project = _parent(tmp_path, "inline")
    (tmp_path / "nested" / "two" / "a.py").write_text("raise ImportError('nope')\n", "utf-8")

    result = await _run(project, monkeypatch)

    assert not result.success
    assert "Nested orchestration n2 failed: Failed to load a" in result.error


_EMPTY_RESPONSE = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=inp.artifact, response_data={})
ENTRY_CLASS = "C"
"""

_SAW = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(artifact=inp.artifact, response_data={"saw": dict(inp.request_data)})
ENTRY_CLASS = "C"
"""


@pytest.mark.parametrize("mode", ["subprocess", "inline"])
async def test_a_child_node_returning_empty_response_data_hands_on_the_previous(
    tmp_path, monkeypatch, mode
):
    child = {
        "nodes": [
            _node("s", "START"),
            _node("a", "AGENT", config={"mainflow_target_node_ref": "b"}),
            _node("b", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("e", "END"),
        ],
        "ports": _ports("s", "a", "b", "e"),
        "edges": [_edge("s", "a")],
    }
    _write(
        tmp_path / "nested" / "child",
        child,
        {"s": _START, "a": _EMPTY_RESPONSE, "b": _SAW, "e": _END},
    )
    graph = {
        "nodes": [
            _node("s", "START"),
            _node(
                "n",
                "ORCHESTRATION",
                project="nested/child",
                config={"execution_mode": mode, "mainflow_target_node_ref": "e"},
            ),
            _node("e", "END"),
        ],
        "ports": _ports("s", "n", "e"),
        "edges": [_edge("s", "n")],
    }
    result = await _run(_write(tmp_path, graph, {"s": _START, "e": _END}), monkeypatch)

    assert result.success, result.error
    # The engine replaces response data only with a non-empty value.
    assert result.response_data == {"saw": {"q": "hi"}}
