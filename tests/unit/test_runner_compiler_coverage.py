"""Coverage tests for orchestration compiler edge cases."""

import pytest

from perceivo.runner.compiler import compile_project

from .test_runner_orchestration_coverage import _build_linear

# ---------------------------------------------------------------------------
# compiler: manifest ports parsing + edge/terminal edge cases
# ---------------------------------------------------------------------------


def test_compiler_parses_manifest_port_types(tmp_workspace):
    _build_linear(tmp_workspace)
    # Overwrite the agent manifest with sectioned ports (incl. a non-dict entry).
    (tmp_workspace / "a.yaml").write_text(
        """
input_ports:
  - {port_name: i, port_type: in}
  - not-a-dict
output_ports:
  - {port_name: o, port_type: out}
  - {port_name: unknown, port_type: mystery}
state_ports:
  - {port_name: st}
policy_ports:
  - {port_name: pol}
input_parameters: []
required_secrets: []
""",
        encoding="utf-8",
    )
    plan = compile_project(str(tmp_workspace))
    shell = plan.shells_by_id["a"]
    assert [p["name"] for p in shell.in_ports] == ["i"]
    assert [p["name"] for p in shell.out_ports] == ["o"]
    assert [p["name"] for p in shell.state_ports] == ["st"]
    assert [p["name"] for p in shell.policy_ports] == ["pol"]


def test_compiler_manifest_ports_not_a_list_ignored(tmp_workspace):
    _build_linear(tmp_workspace)
    (tmp_workspace / "a.yaml").write_text(
        "ports: {}\ninput_parameters: []\nrequired_secrets: []\n",
        encoding="utf-8",
    )
    plan = compile_project(str(tmp_workspace))
    assert plan.shells_by_id["a"].in_ports == []


def test_compiler_edge_cases_raw_yaml(tmp_workspace):
    """Duplicate mainflow, invoke edge, unknown edge kind, dangling node, and a
    missing script file all handled in one compile.

    An ``unknown_port`` edge and a port pointing at an undeclared node raise,
    and are specified with real assertions in
    ``tests/unit/test_runner_compiler.py::test_edge_referencing_unknown_port_raises``
    and ``::test_edge_pointing_at_undeclared_node_raises`` rather than fed here
    for line coverage.
    """
    for n in ("s", "e"):
        (tmp_workspace / f"{n}.py").write_text("# noop\n", encoding="utf-8")
    (tmp_workspace / "b.py").write_text("# noop\n", encoding="utf-8")
    (tmp_workspace / "orchestration.yaml").write_text(
        """
name: edges
nodes:
  - {id: s, component_type: START, name: s, script: s.py}
  - {id: a, component_type: AGENT, name: a, script: nope.py}
  - {id: b, component_type: AGENT, name: b, script: b.py}
  - {id: p, component_type: POLICY, name: p, script: p.py}
  - {id: e, component_type: END, name: e, script: e.py}
ports:
  - {id: ps, node_id: s, name: out}
  - {id: pa_in, node_id: a, name: in}
  - {id: pa_out, node_id: a, name: out}
  - {id: pb_in, node_id: b, name: in}
  - {id: pp, node_id: p, name: policy}
  - {id: pe, node_id: e, name: in}
edges:
  - {source_port_id: ps, target_port_id: pa_in, kind: MAINFLOW}
  - {source_port_id: pa_out, target_port_id: pe, kind: MAINFLOW}
  - {source_port_id: pa_out, target_port_id: pe, kind: MAINFLOW}
  - {source_port_id: pa_out, target_port_id: pp, kind: INVOKE}
  - {source_port_id: ps, target_port_id: pe, kind: WEIRD}
""",
        encoding="utf-8",
    )
    plan = compile_project(str(tmp_workspace))
    a_shell = plan.shells_by_id["a"]
    # Missing script file resolves to None.
    assert a_shell.script_path is None
    # Duplicate MAINFLOW edge deduplicated.
    assert a_shell.mainflow_successors.count("e") == 1
    # INVOKE edge recorded under the source port's name.
    assert "out" in a_shell.invoke_connections
    # Dangling AGENT auto-wired to END.
    assert plan.shells_by_id["b"].mainflow_successors == ["e"]
    assert plan.root_id == "a"


def test_compiler_raises_no_nodes(tmp_workspace):
    (tmp_workspace / "orchestration.yaml").write_text(
        "name: e\nnodes: []\nedges: []\nports: []\n", encoding="utf-8"
    )
    plan_dir = str(tmp_workspace)
    with pytest.raises(RuntimeError, match="no nodes"):
        compile_project(plan_dir)


def test_compiler_raises_no_start(tmp_workspace):
    (tmp_workspace / "orchestration.yaml").write_text(
        "name: x\nnodes:\n"
        "  - {id: a, component_type: AGENT}\n"
        "  - {id: e, component_type: END}\n"
        "edges: []\nports: []\n",
        encoding="utf-8",
    )
    plan_dir = str(tmp_workspace)
    with pytest.raises(RuntimeError, match="No START"):
        compile_project(plan_dir)


def test_compiler_raises_no_end(tmp_workspace):
    (tmp_workspace / "orchestration.yaml").write_text(
        "name: x\nnodes:\n"
        "  - {id: s, component_type: START}\n"
        "  - {id: a, component_type: AGENT}\n"
        "edges: []\nports: []\n",
        encoding="utf-8",
    )
    plan_dir = str(tmp_workspace)
    with pytest.raises(RuntimeError, match="No END"):
        compile_project(plan_dir)
