"""Coverage-focused unit tests for ``perceivo.validator._orchestration._structure``.

Covers mainflow structure (START/END presence, bad targets), what may source an
edge into END, and FSM nesting depth — including the cycles, which must be
rejected without recursing, and the memoization of shared descendants.
"""

from __future__ import annotations

from perceivo.validator._orchestration._structure import (
    _compute_fsm_depths,
    _validate_end_connections,
    _validate_fsm_depth,
    _validate_mainflow_structure,
)

from .fixtures_orch_validator import _type_fn

# ---------------------------------------------------------------------------
# _structure.py
# ---------------------------------------------------------------------------


def test_mainflow_structure_missing_start_and_end_and_bad_target():
    nodes = [
        {"id": "a", "component_type": "AGENT"},
        {"id": "p", "component_type": "POLICY"},
    ]
    ports = [
        {"id": "pa_out", "node_id": "a"},
        {"id": "pp_in", "node_id": "p"},
    ]
    # Mainflow edge whose target is a POLICY node (invalid on mainflow).
    edges = [{"source_port_id": "pa_out", "target_port_id": "pp_in", "kind": "MAINFLOW"}]
    node_type = _type_fn({"a": "AGENT", "p": "POLICY"})
    checks = _validate_mainflow_structure(nodes, edges, ports, node_type)
    ids = {c.id for c in checks}
    assert "ORCH-FLOW-001" in ids  # 0 START nodes
    assert "ORCH-FLOW-002" in ids  # 0 END nodes
    assert any(c.title == "Invalid mainflow component" for c in checks)


def test_mainflow_structure_valid_single_start_end():
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [{"id": "ps", "node_id": "s"}, {"id": "pe", "node_id": "e"}]
    # edge_kind spelled as edge_kind (fallback), target END is allowed.
    edges = [{"source_port_id": "ps", "target_port_id": "pe", "edge_kind": "MAINFLOW"}]
    node_type = _type_fn({"s": "START", "e": "END"})
    checks = _validate_mainflow_structure(nodes, edges, ports, node_type)
    assert checks == []


def test_end_connections_flags_non_mainflow_source():
    nodes = [{"id": "e", "component_type": "END"}, {"id": "pol"}]
    port_map = {
        "src": {"id": "src", "node_id": "pol"},
        "tgt": {"id": "tgt", "node_id": "e"},
        "orphan": {"id": "orphan", "node_id": "nope"},
    }
    edges = [
        {"source_port_id": "src", "target_port_id": "tgt"},  # POLICY -> END : error
        {"source_port_id": "src", "target_port_id": "missing"},  # target_port None : skip
        {"source_port_id": "src", "target_port_id": "orphan"},  # target not END : skip
        {"source_port_id": "missing", "target_port_id": "tgt"},  # source_port None : skip
    ]
    node_type = _type_fn({"e": "END", "pol": "POLICY"})
    checks = _validate_end_connections(nodes, edges, port_map, node_type)
    assert len(checks) == 1
    assert checks[0].title == "Invalid END connection"


def test_end_connections_mainflow_source_ok():
    nodes = [{"id": "e", "component_type": "END"}, {"id": "a"}]
    port_map = {
        "src": {"id": "src", "node_id": "a"},
        "tgt": {"id": "tgt", "node_id": "e"},
    }
    edges = [{"source_port_id": "src", "target_port_id": "tgt"}]
    node_type = _type_fn({"e": "END", "a": "AGENT"})
    assert _validate_end_connections(nodes, edges, port_map, node_type) == []


def test_fsm_depth_no_fsm_short_circuits():
    nodes = [{"id": "a", "component_type": "AGENT"}]
    assert _validate_fsm_depth(nodes, [], [], _type_fn({"a": "AGENT"})) == []


def test_fsm_depth_shallow_chain_ok():
    # Two nested FSMs -> depth 2, well under MAX_FSM_DEPTH.
    nodes = [{"id": "f0"}, {"id": "f1"}]
    ports = [
        {"id": "f0_out", "node_id": "f0"},
        {"id": "f1_in", "node_id": "f1"},
    ]
    edges = [{"source_port_id": "f0_out", "target_port_id": "f1_in", "kind": "INVOKE"}]
    node_type = _type_fn({"f0": "FSM", "f1": "FSM"})
    assert _validate_fsm_depth(nodes, edges, ports, node_type) == []


def test_fsm_depth_exceeds_max_and_handles_cycle():
    # Chain of 12 FSMs via INVOKE edges => depth exceeds MAX_FSM_DEPTH (10),
    # plus a back-edge to exercise the visited-cycle branch.
    n = 12
    nodes = [{"id": f"f{i}", "name": f"f{i}"} for i in range(n)]
    ports = []
    edges = []
    mapping = {}
    for i in range(n):
        mapping[f"f{i}"] = "FSM"
        ports.append({"id": f"f{i}_out", "node_id": f"f{i}"})
        ports.append({"id": f"f{i}_in", "node_id": f"f{i}"})
    for i in range(n - 1):
        edges.append(
            {
                "source_port_id": f"f{i}_out",
                "target_port_id": f"f{i + 1}_in",
                "kind": "INVOKE",
            }
        )
    # Back-edge from last to first -> cycle.
    edges.append({"source_port_id": f"f{n - 1}_out", "target_port_id": "f0_in", "kind": "INVOKE"})
    # Dangling INVOKE edge with a missing port -> continue branch.
    edges.append({"source_port_id": "ghost", "target_port_id": "f0_in", "kind": "INVOKE"})
    node_type = _type_fn(mapping)
    checks = _validate_fsm_depth(nodes, edges, ports, node_type)
    assert any(c.title == "FSM nesting too deep" for c in checks)


def test_fsm_depth_cycle_is_rejected_below_depth_limit():
    nodes = [{"id": "f0"}, {"id": "f1"}]
    ports = [
        {"id": f"{node}_{direction}", "node_id": node}
        for node in ("f0", "f1")
        for direction in ("in", "out")
    ]
    edges = [
        {"source_port_id": "f0_out", "target_port_id": "f1_in", "kind": "INVOKE"},
        {"source_port_id": "f1_out", "target_port_id": "f0_in", "kind": "INVOKE"},
    ]

    checks = _validate_fsm_depth(nodes, edges, ports, _type_fn({"f0": "FSM", "f1": "FSM"}))

    assert len(checks) == 1
    assert checks[0].severity == "error"
    assert checks[0].title == "Circular FSM reference"
    assert checks[0].message in {
        "Circular FSM reference detected involving node f0.",
        "Circular FSM reference detected involving node f1.",
    }


def test_compute_fsm_depths_memoizes_shared_descendants():
    adjacency = {
        "root": {"left", "right"},
        "left": {"shared"},
        "right": {"shared"},
        "shared": {"leaf"},
        "leaf": set(),
    }

    depths, cycle_nodes = _compute_fsm_depths(adjacency)

    assert depths == {"leaf": 1, "shared": 2, "left": 3, "right": 3, "root": 4}
    assert cycle_nodes == set()


def test_compute_fsm_depths_handles_chain_without_recursion():
    node_count = 1500
    adjacency = {
        f"f{i}": ({f"f{i + 1}"} if i < node_count - 1 else set()) for i in range(node_count)
    }

    depths, cycle_nodes = _compute_fsm_depths(adjacency)

    assert depths["f0"] == node_count
    assert depths[f"f{node_count - 1}"] == 1
    assert cycle_nodes == set()
