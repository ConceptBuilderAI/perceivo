"""SPEC-6: local validator ↔ server conformance regression tests.

The platform (the platform data service's ``OrchestrationValidator``) accepts graph
patterns a stricter local validator would reject, because the SDK project format
materializes the mainflow chain as ``MAINFLOW`` edges while the platform stores
mainflow as node config (``mainflow_target_node_id``) and never runs its per-edge
structural checks (connection matrix, single-edge-per-port cap) over them.

These tests pin the reconciled rules — and, importantly, the negative cases
that prove the relaxations did not disable validation of real (INVOKE) edges.

There is no unwired-OUT exemption for a mainflow source or an invoke-target
AGENT: a node on the mainflow already gets a phantom out port from the split of
its bidirectional IN port, so a declared ``out`` port on top of that is an
orphan the canvas draws as an open port. Both validators report it, and the
tests below pin the error plus the no-OUT-port shape.
"""

from __future__ import annotations

from perceivo.validator._orchestration._main import validate_graph


def _report(nodes, ports, edges):
    return validate_graph(nodes=nodes, edges=edges, ports=ports)


# ---------------------------------------------------------------------------
# 1. Mainflow pass-through: an unwired OUT port is an error.
# ---------------------------------------------------------------------------


def test_bidirectional_agent_mainflow_passthrough_out_port_is_an_error():
    """An AGENT that carries mainflow via its ``input`` port must not declare an
    ``output`` port at all.

    Being on the mainflow splits the agent's bidirectional IN port into an in
    port and a phantom out port, and the phantom carries the continuation. A
    declared ``output`` on top of that has nothing to connect to and renders as
    an open port on the canvas, so it is a defect rather than an exemption
    (matches the server).
    """
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "a", "component_type": "AGENT"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_out", "node_id": "s", "port_type": "out"},
        {"id": "s_ext", "node_id": "s", "port_type": "external_in"},
        {"id": "a_in", "node_id": "a", "port_type": "in"},
        {"id": "a_out", "node_id": "a", "port_type": "out"},  # surplus -> error
        {"id": "e_in", "node_id": "e", "port_type": "in"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},
    ]
    # Mainflow pass-through: the agent's IN port both receives and forwards mainflow.
    edges = [
        {"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"},
        {"source_port_id": "a_in", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    report = _report(nodes, ports, edges)
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert len(unwired) == 1, [c.message for c in report.errors]
    assert "a_out" in unwired[0].message


def test_mainflow_passthrough_agent_without_out_port_is_clean():
    """The corrected shape: the same graph with no declared OUT port validates."""
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "a", "component_type": "AGENT"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_out", "node_id": "s", "port_type": "out"},
        {"id": "s_ext", "node_id": "s", "port_type": "external_in"},
        {"id": "a_in", "node_id": "a", "port_type": "in"},
        {"id": "e_in", "node_id": "e", "port_type": "in"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"},
        {"source_port_id": "a_in", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    report = _report(nodes, ports, edges)
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert unwired == [], [c.message for c in unwired]


def test_invoke_target_agent_out_port_is_an_error():
    """An AGENT invoked as a tool returns through its IN port, so a declared and
    unwired OUT port is an open port."""
    nodes = [
        {"id": "f", "component_type": "FSM"},
        {"id": "a", "component_type": "AGENT"},
        {"id": "p", "component_type": "POLICY"},
    ]
    ports = [
        {"id": "f_state", "node_id": "f", "port_type": "state"},
        {"id": "f_pol", "node_id": "f", "port_type": "policy"},
        {"id": "a_in", "node_id": "a", "port_type": "in"},
        {"id": "a_out", "node_id": "a", "port_type": "out"},  # surplus -> error
        {"id": "p_in", "node_id": "p", "port_type": "in"},
    ]
    edges = [
        {"source_port_id": "f_state", "target_port_id": "a_in", "kind": "INVOKE"},
        {"source_port_id": "f_pol", "target_port_id": "p_in", "kind": "INVOKE"},
    ]
    report = _report(nodes, ports, edges)
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert len(unwired) == 1, [c.message for c in report.errors]
    assert "a_out" in unwired[0].message


def test_unwired_out_on_plain_node_still_flagged():
    """An OUT port on a plain TOOL node is reported, as it always was."""
    nodes = [
        {"id": "f", "component_type": "FSM"},
        {"id": "t", "component_type": "TOOL"},
    ]
    ports = [
        {"id": "f_state", "node_id": "f", "port_type": "state"},
        {"id": "t_in", "node_id": "t", "port_type": "in"},
        {"id": "t_out", "node_id": "t", "port_type": "out"},  # TOOL, not exempt
    ]
    edges = [{"source_port_id": "f_state", "target_port_id": "t_in", "kind": "INVOKE"}]
    report = _report(nodes, ports, edges)
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert any("out" in c.message.lower() or "TOOL" in c.message for c in unwired), [
        c.message for c in report.errors
    ]


def _index_only_phantom_hop():
    """A graph whose mainflow hop is spelled only ``mainflow_target_node_index``.

    This is what the canvas persists on the POST that creates an orchestration:
    ``serializeOrchestrationBundle.ts`` always writes the index and writes
    ``mainflow_target_node_id`` only when the target already carries a UUID,
    which it does not before the backend assigns one.
    """
    nodes = [
        {"id": "node_0", "component_type": "START"},
        {
            "id": "node_1",
            "component_type": "AGENT",
            "config": {"mainflow_target_node_index": 2},
        },
        {"id": "node_2", "component_type": "END"},
    ]
    ports = [
        {"id": "s_ext", "node_id": "node_0", "port_type": "external_in"},
        {"id": "s_out", "node_id": "node_0", "port_type": "out"},
        {"id": "a_in", "node_id": "node_1", "port_type": "in"},
        {"id": "e_in", "node_id": "node_2", "port_type": "in"},
        {"id": "e_ext", "node_id": "node_2", "port_type": "external_out"},
    ]
    edges = [{"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"}]
    return nodes, ports, edges


def test_index_only_mainflow_target_is_exempt_like_the_server():
    """The index spelling alone must exempt the target's unwired IN port.

    The server's ``_build_mainflow_sets`` adds ``mainflow_target_node_index``
    raw alongside the other two spellings, so it accepts this graph. Reading
    only ``_ref``/``_id`` made the SDK report ORCH-UNWIRED on the END node of
    every orchestration built in the UI and never re-saved.
    """
    report = _report(*_index_only_phantom_hop())
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert unwired == [], [c.message for c in unwired]


def test_index_zero_mainflow_target_is_exempt():
    """Index zero is a legitimate target and must not be read as absent."""
    nodes = [
        {"id": "node_0", "component_type": "END"},
        {
            "id": "node_1",
            "component_type": "AGENT",
            "config": {"mainflow_target_node_index": 0},
        },
    ]
    ports = [
        {"id": "e_in", "node_id": "node_0", "port_type": "in"},
        {"id": "e_ext", "node_id": "node_0", "port_type": "external_out"},
        {"id": "a_in", "node_id": "node_1", "port_type": "in"},
    ]
    report = _report(nodes, ports, [])
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert not any("e_in" in c.message for c in unwired), [c.message for c in unwired]


def test_unrelated_config_key_does_not_exempt():
    """Only the three known spellings exempt; a lookalike key must not.

    Every key read here suppresses the check this rule exists to run, so a key
    added by mistake is a silent hole rather than a visible error.
    """
    nodes, ports, edges = _index_only_phantom_hop()
    nodes[1]["config"] = {"mainflow_target_node": 2}
    report = _report(nodes, ports, edges)
    unwired = [c for c in report.errors if c.id.startswith("ORCH-UNWIRED")]
    assert any("e_in" in c.message for c in unwired), [c.message for c in report.errors]


# ---------------------------------------------------------------------------
# 2. Policy fan-out: a policy port with one INVOKE and one MAINFLOW edge is OK
# ---------------------------------------------------------------------------


def test_policy_port_invoke_plus_mainflow_exit_not_multi():
    """An FSM policy port that invokes a POLICY component AND exits to END via
    mainflow must not trip ORCH-MULTI (the MAINFLOW edge is not counted)."""
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "f", "component_type": "FSM"},
        {"id": "p", "component_type": "POLICY"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_out", "node_id": "s", "port_type": "out"},
        {"id": "s_ext", "node_id": "s", "port_type": "external_in"},
        {"id": "f_in", "node_id": "f", "port_type": "in"},
        {"id": "f_pol", "node_id": "f", "port_type": "policy"},
        {"id": "p_in", "node_id": "p", "port_type": "in"},
        {"id": "e_in", "node_id": "e", "port_type": "in"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "f_in", "kind": "MAINFLOW"},
        {"source_port_id": "f_pol", "target_port_id": "p_in", "kind": "INVOKE"},
        {"source_port_id": "f_pol", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    report = _report(nodes, ports, edges)
    multi = [c for c in report.errors if c.id.startswith("ORCH-MULTI")]
    assert multi == [], [c.message for c in multi]


def test_two_invoke_edges_on_one_port_still_multi():
    """Negative: two real (INVOKE) edges out of one port is still capped."""
    ports = [
        {"id": "p1", "node_id": "a"},
        {"id": "t1", "node_id": "b"},
        {"id": "t2", "node_id": "c"},
    ]
    edges = [
        {"source_port_id": "p1", "target_port_id": "t1", "kind": "INVOKE"},
        {"source_port_id": "p1", "target_port_id": "t2", "kind": "INVOKE"},
    ]
    from perceivo.validator._orchestration._ports import _validate_single_edge_per_port

    checks = _validate_single_edge_per_port(ports, edges)
    assert any(c.title == "Multiple outgoing edges" for c in checks)


# ---------------------------------------------------------------------------
# 3. AGENT -> ADAPTER via MAINFLOW is accepted; via INVOKE is rejected
# ---------------------------------------------------------------------------


def test_agent_to_adapter_mainflow_allowed():
    """A MAINFLOW transition AGENT -> ADAPTER must not trip ORCH-CONN (mainflow is
    node config on the platform, never checked against the connection matrix)."""
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "a", "component_type": "AGENT"},
        {"id": "d", "component_type": "ADAPTER"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_out", "node_id": "s", "port_type": "out"},
        {"id": "s_ext", "node_id": "s", "port_type": "external_in"},
        {"id": "a_in", "node_id": "a", "port_type": "in"},
        {"id": "a_out", "node_id": "a", "port_type": "out"},
        {"id": "d_in", "node_id": "d", "port_type": "in"},
        {"id": "e_in", "node_id": "e", "port_type": "in"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"},
        {"source_port_id": "a_out", "target_port_id": "d_in", "kind": "MAINFLOW"},
        {"source_port_id": "d_in", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    report = _report(nodes, ports, edges)
    conn = [c for c in report.errors if c.id.startswith("ORCH-CONN")]
    assert conn == [], [c.message for c in conn]


def test_agent_to_adapter_invoke_still_rejected():
    """Negative: an INVOKE edge AGENT -> ADAPTER is still rejected (ADAPTER is not
    in AGENT's allowed set on the server matrix)."""
    nodes = [
        {"id": "a", "component_type": "AGENT"},
        {"id": "d", "component_type": "ADAPTER"},
    ]
    ports = [
        {"id": "a_tool", "node_id": "a", "port_type": "tool"},
        {"id": "d_in", "node_id": "d", "port_type": "in"},
    ]
    edges = [{"source_port_id": "a_tool", "target_port_id": "d_in", "kind": "INVOKE"}]
    report = _report(nodes, ports, edges)
    assert any(c.title == "Invalid connection" for c in report.errors)


def test_agent_to_orchestration_invoke_allowed():
    """AGENT -> ORCHESTRATION (nested orchestration) is allowed on the
    server matrix; the local matrix must match."""
    from perceivo.validator._orchestration._constants import ALLOWED_CONNECTIONS

    assert "ORCHESTRATION" in ALLOWED_CONNECTIONS["AGENT"]
    assert "ORCHESTRATION" in ALLOWED_CONNECTIONS["FSM"]
    assert "LAYER" not in ALLOWED_CONNECTIONS
    assert all("LAYER" not in targets for targets in ALLOWED_CONNECTIONS.values())
