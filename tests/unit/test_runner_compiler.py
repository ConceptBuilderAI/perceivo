"""T6: Runner compiler (``compile_project`` on disk, ``compile_orchestration`` in memory)."""

from __future__ import annotations

import copy
import pathlib

import pytest
import yaml

from perceivo.orchestration._builder import Orchestration
from perceivo.runner import compiler
from perceivo.runner.compiler import compile_orchestration, compile_project


def _minimal_orch_dir(workspace):
    orch = Orchestration("rc")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml", node_id="a")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, a)
    orch.add_edge(a, e)
    # Provide manifests so loader doesn't drop ports.
    for n, body in [
        ("s", "ports: []\n"),
        ("a", "ports: []\n"),
        ("e", "ports: []\n"),
    ]:
        (workspace / f"{n}.yaml").write_text(body, encoding="utf-8")
        (workspace / f"{n}.py").write_text("# noop\n", encoding="utf-8")
    orch.save(str(workspace))
    return orch


def test_compile_minimal_orchestration(tmp_workspace):
    _minimal_orch_dir(tmp_workspace)
    result = compile_project(str(tmp_workspace))
    assert result.start_node_id == "s"
    assert result.end_node_id == "e"
    assert result.root_id == "a"
    assert set(result.shells_by_id.keys()) == {"s", "a", "e"}


def test_compile_raises_when_no_orchestration_yaml(tmp_workspace):
    workspace_path = str(tmp_workspace)
    with pytest.raises(FileNotFoundError, match=r"orchestration.yaml not found"):
        compile_project(workspace_path)


def test_compile_raises_when_no_nodes(tmp_workspace):
    (tmp_workspace / "orchestration.yaml").write_text(
        "name: empty\nnodes: []\nedges: []\nports: []\n", encoding="utf-8"
    )
    workspace_path = str(tmp_workspace)
    with pytest.raises(RuntimeError, match="no nodes"):
        compile_project(workspace_path)


def test_compile_raises_when_no_start(tmp_workspace):
    (tmp_workspace / "orchestration.yaml").write_text(
        "name: x\n"
        "nodes:\n"
        "  - id: a\n    component_type: AGENT\n"
        "  - id: e\n    component_type: END\n"
        "edges: []\nports: []\n",
        encoding="utf-8",
    )
    workspace_path = str(tmp_workspace)
    with pytest.raises(RuntimeError, match="No START"):
        compile_project(workspace_path)


def test_compile_raises_when_no_end(tmp_workspace):
    (tmp_workspace / "orchestration.yaml").write_text(
        "name: x\n"
        "nodes:\n"
        "  - id: s\n    component_type: START\n"
        "  - id: a\n    component_type: AGENT\n"
        "edges: []\nports: []\n",
        encoding="utf-8",
    )
    workspace_path = str(tmp_workspace)
    with pytest.raises(RuntimeError, match="No END"):
        compile_project(workspace_path)


def test_terminal_node_auto_connected_to_end(tmp_workspace):
    """A node with no mainflow successors should be wired to END."""
    _minimal_orch_dir(tmp_workspace)
    result = compile_project(str(tmp_workspace))
    # Both s->a and a->e edges exist; "a" should still have e as successor.
    assert "e" in result.shells_by_id["a"].mainflow_successors


def test_invoke_edge_recorded_as_connection(tmp_workspace):
    orch = Orchestration("inv")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    f = orch.add_node("f", "FSM", script="f.py", manifest="f.yaml", node_id="f")
    p = orch.add_node("p", "POLICY", script="p.py", manifest="p.yaml", node_id="p")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, f)
    orch.add_edge(f, e)
    orch.add_edge(f, p, source_port="policy")  # auto-infers INVOKE

    for nm in ("s", "f", "p", "e"):
        (tmp_workspace / f"{nm}.yaml").write_text("ports: []\n", encoding="utf-8")
        (tmp_workspace / f"{nm}.py").write_text("# noop\n", encoding="utf-8")
    orch.save(str(tmp_workspace))

    result = compile_project(str(tmp_workspace))
    fsm_shell = result.shells_by_id["f"]
    assert "policy" in fsm_shell.invoke_connections
    conns = fsm_shell.invoke_connections["policy"]
    assert any(c.to_node_id == "p" for c in conns)


# --------------------------------------------------------------------------
# An edge that cannot be wired must raise, never be dropped.
#
# These are written as raw orchestration.yaml rather than through
# ``Orchestration``, because the builder will not emit a dangling edge: the
# defect only reaches the compiler from a hand-written or platform-exported
# graph, which is exactly the path the seed corpus takes.
# --------------------------------------------------------------------------

_THREE_NODES = (
    "nodes:\n"
    "  - id: s\n    component_type: START\n"
    "  - id: a\n    component_type: AGENT\n"
    "  - id: e\n    component_type: END\n"
)


def _write_orch(workspace, body: str) -> str:
    (workspace / "orchestration.yaml").write_text(f"name: x\n{body}", encoding="utf-8")
    return str(workspace)


def test_edge_referencing_unknown_port_raises(tmp_workspace):
    path = _write_orch(
        tmp_workspace,
        _THREE_NODES + "ports: []\n"
        "edges:\n"
        "  - source_port_id: ghost_out\n    target_port_id: ghost_in\n    kind: MAINFLOW\n",
    )
    with pytest.raises(RuntimeError, match=r"ghost_out.*does not match any declared port"):
        compile_project(path)


def test_edge_port_with_no_node_id_raises(tmp_workspace):
    path = _write_orch(
        tmp_workspace,
        _THREE_NODES + "ports:\n"
        "  - id: p_orphan\n    node_id: ''\n    name: out\n    port_type: out\n"
        "  - id: p_a_in\n    node_id: a\n    name: input\n    port_type: in\n"
        "edges:\n"
        "  - source_port_id: p_orphan\n    target_port_id: p_a_in\n    kind: MAINFLOW\n",
    )
    with pytest.raises(RuntimeError, match=r"p_orphan.*no node_id"):
        compile_project(path)


def test_edge_pointing_at_undeclared_node_raises(tmp_workspace):
    path = _write_orch(
        tmp_workspace,
        _THREE_NODES + "ports:\n"
        "  - id: p_s_out\n    node_id: s\n    name: output\n    port_type: out\n"
        "  - id: p_ghost_in\n    node_id: nowhere\n    name: input\n    port_type: in\n"
        "edges:\n"
        "  - source_port_id: p_s_out\n    target_port_id: p_ghost_in\n    kind: MAINFLOW\n",
    )
    with pytest.raises(RuntimeError, match=r"'nowhere'.*not a declared node"):
        compile_project(path)


def test_wireable_edge_still_compiles(tmp_workspace):
    """Guard against over-eager raising: a resolvable edge must still wire."""
    path = _write_orch(
        tmp_workspace,
        _THREE_NODES + "ports:\n"
        "  - id: p_s_out\n    node_id: s\n    name: output\n    port_type: out\n"
        "  - id: p_a_in\n    node_id: a\n    name: input\n    port_type: in\n"
        "edges:\n"
        "  - source_port_id: p_s_out\n    target_port_id: p_a_in\n    kind: MAINFLOW\n",
    )
    result = compile_project(path)
    assert result.shells_by_id["s"].mainflow_successors == ["a"]
    assert result.root_id == "a"


# --------------------------------------------------------------------------
# Duplicate terminals are ambiguous, not merely untidy.
#
# Under the previous "last match wins" rule these compiled clean and the
# orchestration that ran depended on node declaration order.
# --------------------------------------------------------------------------


def test_two_start_nodes_rejected(tmp_workspace):
    path = _write_orch(
        tmp_workspace,
        "nodes:\n"
        "  - id: s1\n    component_type: START\n"
        "  - id: s2\n    component_type: START\n"
        "  - id: e\n    component_type: END\n"
        "ports: []\nedges: []\n",
    )
    with pytest.raises(RuntimeError, match=r"2 START nodes \(s1, s2\)"):
        compile_project(path)


def test_two_end_nodes_rejected(tmp_workspace):
    path = _write_orch(
        tmp_workspace,
        "nodes:\n"
        "  - id: s\n    component_type: START\n"
        "  - id: e1\n    component_type: END\n"
        "  - id: e2\n    component_type: END\n"
        "ports: []\nedges: []\n",
    )
    with pytest.raises(RuntimeError, match=r"2 END nodes \(e1, e2\)"):
        compile_project(path)


def test_missing_terminal_messages_unchanged(tmp_workspace):
    """The absent-terminal errors stay in the caller, with their original text."""
    path = _write_orch(
        tmp_workspace,
        "nodes:\n  - id: a\n    component_type: AGENT\n  - id: e\n    component_type: END\n"
        "ports: []\nedges: []\n",
    )
    with pytest.raises(RuntimeError, match="No START node found"):
        compile_project(path)


# --------------------------------------------------------------------------- #
# The compiler takes a graph, so a production engine can call it              #
# --------------------------------------------------------------------------- #

_GRAPH = {
    "name": "in-memory",
    "nodes": [
        {"id": "s", "component_type": "START", "script": "s.py", "manifest": "s.yaml"},
        {"id": "a", "component_type": "AGENT", "script": "a.py", "manifest": "a.yaml"},
        {"id": "e", "component_type": "END", "script": "e.py", "manifest": "e.yaml"},
    ],
    "ports": [
        {"id": "p_s", "node_id": "s", "name": "output", "key": "output"},
        {"id": "p_a_in", "node_id": "a", "name": "input", "key": "input"},
        {"id": "p_a_out", "node_id": "a", "name": "output", "key": "output"},
        {"id": "p_e", "node_id": "e", "name": "input", "key": "input"},
    ],
    "edges": [
        {"source_port_id": "p_s", "target_port_id": "p_a_in", "kind": "MAINFLOW"},
        {"source_port_id": "p_a_out", "target_port_id": "p_e", "kind": "MAINFLOW"},
    ],
}


def test_a_graph_never_written_to_disk_compiles():
    # A production engine holds the graph in memory and has no directory to
    # hand over, so the shared compiler must accept the graph itself.
    result = compile_orchestration(_GRAPH)
    assert (result.start_node_id, result.root_id, result.end_node_id) == ("s", "a", "e")
    assert result.shells_by_id["s"].mainflow_successors == ["a"]
    assert result.shells_by_id["a"].mainflow_successors == ["e"]


def test_a_directory_path_is_refused_and_names_its_replacement():
    # The refusal has to say what to call instead. Handed a path, the walk
    # would otherwise raise AttributeError: 'str' object has no attribute 'get'
    # from deep inside.
    with pytest.raises(TypeError, match="compile_project"):
        compile_orchestration("./some-project")
    with pytest.raises(TypeError, match="compile_project"):
        compile_orchestration(pathlib.Path("./some-project"))


def test_a_graph_only_compile_does_not_bind_the_working_directory(tmp_path, monkeypatch):
    # Two cwds, one of which holds files matching the graph's relative names.
    # Resolving against the cwd would make the same graph compile to different
    # scripts depending on where the process was launched — a divergence between
    # the local and production executors that neither one records.
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "a.py").write_bytes(b"# decoy\n")
    (decoy / "a.yaml").write_bytes(b"ports: []\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(decoy)
    from_decoy = compile_orchestration(_GRAPH)
    monkeypatch.chdir(elsewhere)
    from_elsewhere = compile_orchestration(_GRAPH)

    assert from_decoy.shells_by_id["a"].script_path is None
    assert from_decoy.shells_by_id["a"].script_path == from_elsewhere.shells_by_id["a"].script_path


def test_base_dir_resolves_the_relative_component_files(tmp_path):
    for stem in ("s", "a", "e"):
        (tmp_path / f"{stem}.py").write_bytes(b"# noop\n")
        (tmp_path / f"{stem}.yaml").write_bytes(b"ports: []\n")

    result = compile_orchestration(_GRAPH, base_dir=tmp_path)
    script = result.shells_by_id["a"].script_path
    assert script is not None
    assert pathlib.Path(script) == tmp_path.resolve() / "a.py"


def test_an_absolute_script_resolves_with_no_base_dir(tmp_path):
    script = tmp_path / "a.py"
    script.write_bytes(b"# noop\n")
    graph = copy.deepcopy(_GRAPH)
    for node in graph["nodes"]:
        if node["id"] == "a":
            node["script"] = str(script)
            node["manifest"] = None

    result = compile_orchestration(graph)
    assert result.shells_by_id["a"].script_path == str(script)


def test_compile_project_is_the_same_compile_over_the_parsed_file(tmp_workspace):
    _minimal_orch_dir(tmp_workspace)
    graph = yaml.safe_load((tmp_workspace / "orchestration.yaml").read_text(encoding="utf-8"))

    from_disk = compile_project(str(tmp_workspace))
    from_graph = compile_orchestration(graph, base_dir=tmp_workspace)

    assert from_disk.shells_by_id.keys() == from_graph.shells_by_id.keys()
    assert {k: v.script_path for k, v in from_disk.shells_by_id.items()} == {
        k: v.script_path for k, v in from_graph.shells_by_id.items()
    }
    assert from_disk.root_id == from_graph.root_id


def test_the_compiler_module_declares_both_entrypoints():
    # The shared compiler the production engine imports states its own contract, so a later
    # refactor cannot quietly stop exporting the name it is imported for.
    assert compiler.__all__ == ["compile_orchestration", "compile_project"]
    for name in compiler.__all__:
        assert callable(getattr(compiler, name))
