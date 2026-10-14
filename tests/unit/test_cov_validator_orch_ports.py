"""Coverage-focused unit tests for ``perceivo.validator._orchestration._ports``.

Covers the per-port rules: the single-edge cap — including the combined
source+target count and the mainflow pass-through exception — port
connectivity, the FSM policy-port requirement, and FSM/LAYER state ports
needing an INVOKE handler.
"""

from __future__ import annotations

from perceivo.validator._orchestration._ports import (
    _validate_all_ports_connected,
    _validate_fsm_requires_policy,
    _validate_fsm_state_ports_connected,
    _validate_single_edge_per_port,
)

from .fixtures_orch_validator import _type_fn

# ---------------------------------------------------------------------------
# _ports.py
# ---------------------------------------------------------------------------


def test_single_edge_per_port_flags_multi_out_and_multi_in():
    ports = [
        {"id": "p1", "name": "P1"},
        {"id": "p2"},
        {"id": "p3"},
        {"id": "t1", "name": "T1"},
    ]
    # INVOKE edges: the per-port cap applies to real edges. (MAINFLOW edges are
    # exempt — mainflow lives in node config on the platform, not the edge list.)
    edges = [
        {"source_port_id": "p1", "target_port_id": "t1", "kind": "INVOKE"},
        {"source_port_id": "p1", "target_port_id": "t2", "kind": "INVOKE"},  # p1 has 2 outgoing
        {"source_port_id": "p9", "target_port_id": "t1", "kind": "INVOKE"},  # t1 has 2 incoming
    ]
    checks = _validate_single_edge_per_port(ports, edges)
    titles = {c.title for c in checks}
    assert "Multiple outgoing edges" in titles
    assert "Multiple incoming edges" in titles
    # Port name lookup used for known ports.
    assert any("'P1'" in c.message for c in checks)


def test_single_edge_per_port_clean():
    ports = [{"id": "a"}, {"id": "b"}]
    edges = [{"source_port_id": "a", "target_port_id": "b"}]
    assert _validate_single_edge_per_port(ports, edges) == []


def test_all_ports_connected_skips_external_and_flags_dangling():
    ports = [
        {"id": "s_ext", "node_id": "s", "port_type": "external_in"},  # START ext_in -> skip
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},  # END ext_out -> skip
        {"id": "wired", "node_id": "a", "port_type": "in"},  # connected -> skip
        {"id": "dangle", "node_id": "a", "port_type": "out"},  # unconnected -> error
    ]
    # No OUT port is exempt: 'a' receives mainflow on 'wired', which is
    # exactly the case where its phantom out port carries the continuation, so the
    # declared 'dangle' port has nothing to connect to and is flagged.
    edges = [{"source_port_id": "other", "target_port_id": "wired", "kind": "MAINFLOW"}]
    node_type = _type_fn({"s": "START", "e": "END", "a": "AGENT"})
    checks = _validate_all_ports_connected(ports, edges, node_type)
    assert len(checks) == 1
    assert checks[0].title == "Unconnected port"
    assert "AGENT" in checks[0].message


def test_fsm_requires_policy_missing_port():
    nodes = [{"id": "f", "name": "fsm1"}]
    ports = [{"id": "fin", "node_id": "f", "port_type": "in"}]
    edges = []
    node_type = _type_fn({"f": "FSM"})
    checks = _validate_fsm_requires_policy(nodes, ports, edges, node_type)
    assert len(checks) == 1
    assert checks[0].title == "FSM missing policy"


def test_fsm_requires_policy_connected_to_policy_passes():
    nodes = [{"id": "f"}, {"id": "p"}]
    ports = [
        {"id": "fpol", "node_id": "f", "port_type": "policy"},
        {"id": "pin", "node_id": "p", "port_type": "in"},
    ]
    # The policy port must be wired to the POLICY component via an INVOKE edge.
    edges = [{"source_port_id": "fpol", "target_port_id": "pin", "kind": "INVOKE"}]
    node_type = _type_fn({"f": "FSM", "p": "POLICY"})
    assert _validate_fsm_requires_policy(nodes, ports, edges, node_type) == []


def test_fsm_requires_policy_mainflow_edge_does_not_satisfy():
    # A MAINFLOW edge to the POLICY component does NOT wire the policy port
    # because only an INVOKE edge counts.
    nodes = [{"id": "f", "name": "fsm1"}, {"id": "p"}]
    ports = [
        {"id": "fpol", "node_id": "f", "port_type": "policy"},
        {"id": "pin", "node_id": "p", "port_type": "in"},
    ]
    edges = [{"source_port_id": "fpol", "target_port_id": "pin", "kind": "MAINFLOW"}]
    node_type = _type_fn({"f": "FSM", "p": "POLICY"})
    checks = _validate_fsm_requires_policy(nodes, ports, edges, node_type)
    assert len(checks) == 1
    assert checks[0].title == "FSM policy not connected"


def test_fsm_requires_policy_unconnected_policy_port_errors():
    nodes = [{"id": "f", "name": "fsm1"}]
    ports = [{"id": "fpol", "node_id": "f", "port_type": "policy"}]
    # Edge from the policy port points at a non-existent target port.
    edges = [{"source_port_id": "fpol", "target_port_id": "ghost"}]
    node_type = _type_fn({"f": "FSM"})
    checks = _validate_fsm_requires_policy(nodes, ports, edges, node_type)
    assert len(checks) == 1
    assert checks[0].title == "FSM policy not connected"


# ---------------------------------------------------------------------------
# _ports.py :: _validate_fsm_state_ports_connected
# ---------------------------------------------------------------------------


def test_fsm_state_port_with_invoke_handler_passes():
    nodes = [{"id": "f", "name": "fsm1"}, {"id": "h"}]
    ports = [
        {"id": "fstate", "node_id": "f", "port_type": "state"},
        {"id": "hin", "node_id": "h", "port_type": "in"},
    ]
    edges = [{"source_port_id": "fstate", "target_port_id": "hin", "kind": "INVOKE"}]
    node_type = _type_fn({"f": "FSM", "h": "AGENT"})
    assert _validate_fsm_state_ports_connected(nodes, ports, edges, node_type) == []


def test_fsm_state_port_without_handler_errors():
    nodes = [{"id": "f", "name": "fsm1"}]
    ports = [{"id": "fstate", "node_id": "f", "port_type": "state"}]
    edges = []
    node_type = _type_fn({"f": "FSM"})
    checks = _validate_fsm_state_ports_connected(nodes, ports, edges, node_type)
    assert len(checks) == 1
    assert checks[0].title == "FSM state port has no handler"


def test_fsm_state_port_mainflow_edge_is_not_a_handler():
    # A MAINFLOW edge out of a state port is not a handler wire.
    nodes = [{"id": "f", "name": "fsm1"}, {"id": "h"}]
    ports = [
        {"id": "fstate", "node_id": "f", "port_type": "state"},
        {"id": "hin", "node_id": "h", "port_type": "in"},
    ]
    edges = [{"source_port_id": "fstate", "target_port_id": "hin", "kind": "MAINFLOW"}]
    node_type = _type_fn({"f": "FSM", "h": "AGENT"})
    checks = _validate_fsm_state_ports_connected(nodes, ports, edges, node_type)
    assert len(checks) == 1
    assert checks[0].title == "FSM state port has no handler"


def test_layer_is_not_checked_as_an_fsm():
    # LAYER is not an alias of FSM: its state ports are not FSM state ports.
    nodes = [{"id": "ly", "name": "layer1"}]
    ports = [{"id": "lstate", "node_id": "ly", "port_type": "state"}]
    edges = []
    node_type = _type_fn({"ly": "LAYER"})
    assert _validate_fsm_state_ports_connected(nodes, ports, edges, node_type) == []


# ---------------------------------------------------------------------------
# _ports.py :: _validate_single_edge_per_port (combined cap)
# ---------------------------------------------------------------------------


def test_single_edge_combined_source_and_target_errors():
    # A port that is both the source of one INVOKE edge and the target of another
    # (each within the per-direction cap) still exceeds the combined cap of 1.
    ports = [
        {"id": "px", "name": "px"},
        {"id": "pa", "name": "pa"},
        {"id": "pb", "name": "pb"},
    ]
    edges = [
        {"source_port_id": "px", "target_port_id": "pa", "kind": "INVOKE"},
        {"source_port_id": "pb", "target_port_id": "px", "kind": "INVOKE"},
    ]
    checks = _validate_single_edge_per_port(ports, edges)
    assert any(c.title == "Multiple edges on port" for c in checks)


def test_single_edge_mainflow_passthrough_not_capped():
    # A mainflow pass-through port (in + out via MAINFLOW) is not capped — mainflow
    # lives in node config on the platform, not as real edges (SPEC-6).
    ports = [{"id": "px", "name": "px"}, {"id": "pa"}, {"id": "pb"}]
    edges = [
        {"source_port_id": "pa", "target_port_id": "px", "kind": "MAINFLOW"},
        {"source_port_id": "px", "target_port_id": "pb", "kind": "MAINFLOW"},
    ]
    assert _validate_single_edge_per_port(ports, edges) == []
