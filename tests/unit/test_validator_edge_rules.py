"""The SDK validator must enforce the server's per-edge rule table.

Each rule below was measured as a real divergence against the server's edge
validation: the server rejected the shape and the SDK stayed silent. The guard tests pin the shapes the
SDK must keep accepting, so closing the gap cannot turn into over-rejection.
"""

from __future__ import annotations

from typing import Any

from perceivo.validator._orchestration._main import validate_graph

GraphRows = list[dict[str, Any]]


def _errors(nodes: GraphRows, ports: GraphRows, edges: GraphRows) -> list[str]:
    report = validate_graph(
        nodes=nodes,
        edges=edges,
        ports=ports,
        component_types={n["id"]: n["component_type"] for n in nodes},
    )
    return [f"[{c.id}] {c.message}" for c in report.checks if c.severity == "error"]


def _ids(errors: list[str], stem: str) -> list[str]:
    return [e for e in errors if e.startswith(f"[{stem}-")]


def _agent_chain() -> tuple[GraphRows, GraphRows, GraphRows]:
    """START -> AGENT -(phantom mainflow)-> END. Valid on both validators."""
    nodes: GraphRows = [
        {"id": "n0", "component_type": "START"},
        {"id": "n1", "component_type": "AGENT", "config": {"mainflow_target_node_ref": "n2"}},
        {"id": "n2", "component_type": "END"},
    ]
    ports: GraphRows = [
        {"id": "s_ext", "node_id": "n0", "key": "trigger", "port_type": "external_in"},
        {"id": "s_out", "node_id": "n0", "key": "output", "port_type": "out"},
        {"id": "a_in", "node_id": "n1", "key": "input", "port_type": "in"},
        {"id": "e_in", "node_id": "n2", "key": "input", "port_type": "in"},
        {"id": "e_ext", "node_id": "n2", "key": "result", "port_type": "external_out"},
    ]
    edges: GraphRows = [{"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"}]
    return nodes, ports, edges


def _agent_with_tool() -> tuple[GraphRows, GraphRows, GraphRows]:
    """The agent chain plus a TOOL the agent invokes through its ``tool`` port."""
    nodes, ports, edges = _agent_chain()
    nodes.append({"id": "n3", "component_type": "TOOL"})
    ports.append({"id": "a_tool", "node_id": "n1", "key": "llm", "port_type": "tool"})
    ports.append({"id": "t_in", "node_id": "n3", "key": "input", "port_type": "in"})
    edges.append({"source_port_id": "a_tool", "target_port_id": "t_in", "kind": "INVOKE"})
    return nodes, ports, edges


def _fsm_graph() -> tuple[GraphRows, GraphRows, GraphRows]:
    """START -> FSM -(phantom mainflow)-> END, with a policy and a state handler."""
    nodes: GraphRows = [
        {"id": "n0", "component_type": "START"},
        {"id": "n1", "component_type": "FSM", "config": {"mainflow_target_node_ref": "n2"}},
        {"id": "n2", "component_type": "END"},
        {"id": "n3", "component_type": "POLICY"},
        {"id": "n4", "component_type": "AGENT"},
    ]
    ports: GraphRows = [
        {"id": "s_ext", "node_id": "n0", "key": "trigger", "port_type": "external_in"},
        {"id": "s_out", "node_id": "n0", "key": "output", "port_type": "out"},
        {"id": "f_in", "node_id": "n1", "key": "input", "port_type": "in"},
        {"id": "f_pol", "node_id": "n1", "key": "policy", "port_type": "policy"},
        {"id": "f_state", "node_id": "n1", "key": "state_a", "port_type": "state"},
        {"id": "e_in", "node_id": "n2", "key": "input", "port_type": "in"},
        {"id": "e_ext", "node_id": "n2", "key": "result", "port_type": "external_out"},
        {"id": "p_in", "node_id": "n3", "key": "input", "port_type": "in"},
        {"id": "ag_in", "node_id": "n4", "key": "input", "port_type": "in"},
    ]
    edges: GraphRows = [
        {"source_port_id": "s_out", "target_port_id": "f_in", "kind": "MAINFLOW"},
        {"source_port_id": "f_pol", "target_port_id": "p_in", "kind": "INVOKE"},
        {"source_port_id": "f_state", "target_port_id": "ag_in", "kind": "INVOKE"},
    ]
    return nodes, ports, edges


# --------------------------------------------------------------------------
# Baselines: every mutation below starts from a graph the SDK accepts, so a
# failing assertion can only mean the mutation itself was (not) caught.
# --------------------------------------------------------------------------


def test_baselines_are_valid() -> None:
    assert _errors(*_agent_chain()) == []
    assert _errors(*_agent_with_tool()) == []
    assert _errors(*_fsm_graph()) == []


# --------------------------------------------------------------------------
# ORCH-MFCONT -- only a mainflow-capable component may continue the mainflow
# --------------------------------------------------------------------------


def test_tool_cannot_continue_the_mainflow_via_config() -> None:
    """A TOOL has no phantom out-port, so it cannot be chained.

    The server rejects this on import: "Node 'n1' declares a mainflow
    continuation but its type 'TOOL' has no phantom out-port."
    """
    nodes, ports, edges = _agent_chain()
    nodes[1] = {
        "id": "n1",
        "component_type": "TOOL",
        "config": {"mainflow_target_node_ref": "n2"},
    }

    errors = _errors(nodes, ports, edges)
    # Assert the message on the rule's own finding, not on ``errors[0]``.
    # The merge that folded the parity rules into ``_edges.py`` also brought
    # ``_structure.py``'s ORCH-FLOW-003, which judges the same graph and
    # sorts ahead of this one -- so ``errors[0]`` began reading a message
    # from a different rule while both assertions still passed.
    mfcont = _ids(errors, "ORCH-MFCONT")
    assert mfcont, errors
    assert any("has no phantom out-port" in e for e in mfcont), mfcont


def test_tool_cannot_continue_the_mainflow_via_a_declared_out_port() -> None:
    """The other spelling: the bundle writer converts it to the same defect.

    ``perceivo/_bundle/orchestration.py`` line 228 rewrites an AGENT/TOOL out-port
    MAINFLOW edge into ``config.mainflow_target_node_ref``, so this exports as a
    TOOL continuation and the server rejects it there. The SDK must catch it here.
    """
    nodes, ports, edges = _agent_chain()
    nodes[1] = {"id": "n1", "component_type": "TOOL", "config": {}}
    ports.append({"id": "t_out", "node_id": "n1", "key": "output", "port_type": "out"})
    edges.append({"source_port_id": "t_out", "target_port_id": "e_in", "kind": "MAINFLOW"})

    assert _ids(_errors(nodes, ports, edges), "ORCH-MFCONT")


def test_agent_may_continue_the_mainflow_from_a_declared_out_port() -> None:
    """Guard: this is the SDK project format's normal authoring spelling.

    The bundle writer rewrites it into ``config.mainflow_target_node_ref`` before
    the server ever sees it (``_bundle/orchestration.py`` line 228), so enforcing
    the server's VAL-MAINFLOW-OUT-001 against it would reject all six shipped
    templates while changing nothing about what the platform receives.
    """
    nodes, ports, edges = _agent_chain()
    nodes[1]["config"] = {}
    ports.append({"id": "a_out", "node_id": "n1", "key": "output", "port_type": "out"})
    edges.append({"source_port_id": "a_out", "target_port_id": "e_in", "kind": "MAINFLOW"})

    assert _errors(nodes, ports, edges) == []


def test_virtual_mainflow_out_port_is_accepted() -> None:
    """The reader's synthesized phantom port must not be double-reported.

    ``bundle_to_orchestration`` renders ``config.mainflow_target_node_ref`` as a
    ``virtual`` out port carrying a MAINFLOW edge. Flagging that shape would make
    the SDK reject graphs it just produced from bundles the platform accepts.
    """
    nodes, ports, edges = _agent_chain()
    nodes[1]["config"] = {}
    ports.append(
        {
            "id": "virtual_n1_out",
            "node_id": "n1",
            "key": "output",
            "port_type": "out",
            "direction": "out",
            "virtual": True,
        }
    )
    edges.append({"source_port_id": "virtual_n1_out", "target_port_id": "e_in", "kind": "MAINFLOW"})

    assert _errors(nodes, ports, edges) == []


# --------------------------------------------------------------------------
# Layered source -- server _layered_source_error (helpers lines 162-175)
# --------------------------------------------------------------------------


def test_agent_out_port_cannot_invoke_a_tool() -> None:
    nodes, ports, edges = _agent_with_tool()
    for port in ports:
        if port["id"] == "a_tool":
            port["port_type"] = "out"

    errors = _errors(nodes, ports, edges)
    assert _ids(errors, "ORCH-FSM"), errors


def test_agent_tool_port_may_invoke_a_tool() -> None:
    """Guard: the ``tool`` port is direction OUT but is the legitimate spelling."""
    assert _errors(*_agent_with_tool()) == []


# --------------------------------------------------------------------------
# Direction -- server _direction_errors (helpers lines 118-159)
# --------------------------------------------------------------------------


def test_in_port_cannot_source_an_invoke_edge() -> None:
    nodes, ports, edges = _agent_with_tool()
    nodes.append({"id": "n5", "component_type": "TOOL"})
    ports.append({"id": "t_bad", "node_id": "n3", "key": "passthru", "port_type": "in"})
    ports.append({"id": "t5_in", "node_id": "n5", "key": "input", "port_type": "in"})
    edges.append({"source_port_id": "t_bad", "target_port_id": "t5_in", "kind": "INVOKE"})

    errors = _errors(nodes, ports, edges)
    assert _ids(errors, "ORCH-DIR"), errors
    assert "must be OUT, bidirectional, or an invoke port" in errors[0]


def test_out_port_cannot_be_an_edge_target() -> None:
    nodes, ports, edges = _agent_with_tool()
    for port in ports:
        if port["id"] == "t_in":
            port["port_type"] = "out"

    errors = _errors(nodes, ports, edges)
    assert any("must be IN or bidirectional" in e for e in _ids(errors, "ORCH-DIR")), errors


def test_agent_in_port_may_source_the_mainflow() -> None:
    """Guard: the SDK's mainflow pass-through spelling stays legal on an AGENT.

    The server allows an IN-direction MAINFLOW source for AGENT/FSM/ADAPTER/
    ORCHESTRATION (helpers lines 136-142); the SDK must allow exactly the same.
    """
    nodes, ports, edges = _agent_chain()
    nodes[1]["config"] = {}
    edges.append({"source_port_id": "a_in", "target_port_id": "e_in", "kind": "MAINFLOW"})

    assert _errors(nodes, ports, edges) == []


# --------------------------------------------------------------------------
# Bidirectional pairing -- server _bidir_mismatch_errors (helpers lines 253-280)
# --------------------------------------------------------------------------


def test_state_port_cannot_mainflow_to_a_unidirectional_port() -> None:
    nodes, ports, edges = _fsm_graph()
    nodes[1]["config"] = {}
    edges.append({"source_port_id": "f_state", "target_port_id": "e_in", "kind": "MAINFLOW"})

    errors = _errors(nodes, ports, edges)
    # Same reason as ORCH-MFCONT above: ``_ports.py``'s ORCH-END-001 also
    # rejects this graph and takes position 0.
    bidir = _ids(errors, "ORCH-BIDIR")
    assert bidir, errors
    assert any("bidirectional port" in e for e in bidir), bidir


def test_state_port_may_invoke_a_unidirectional_handler() -> None:
    """Guard: INVOKE edges are exempt -- the normal FSM dispatch pattern."""
    assert _errors(*_fsm_graph()) == []


# --------------------------------------------------------------------------
# port_type per category -- server _validate_port_types_per_category
# (_port_validation.py lines 283-328, table at __init__.py lines 57-68)
# --------------------------------------------------------------------------


def test_state_port_on_an_agent_is_rejected() -> None:
    nodes, ports, edges = _fsm_graph()
    for port in ports:
        if port["id"] == "ag_in":
            port["port_type"] = "state"

    errors = _errors(nodes, ports, edges)
    assert _ids(errors, "ORCH-PORTTYPE"), errors
    assert "AGENT allows" in " ".join(errors)


def test_tool_port_on_an_fsm_is_rejected() -> None:
    nodes, ports, edges = _fsm_graph()
    for port in ports:
        if port["id"] == "f_pol":
            port["port_type"] = "tool"

    assert _ids(_errors(nodes, ports, edges), "ORCH-PORTTYPE")
