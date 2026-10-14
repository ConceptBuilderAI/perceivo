"""T5: Orchestration validator (validate_orchestration)."""

from __future__ import annotations

from perceivo.validator import validate_orchestration
from perceivo.validator._orchestration._main import validate_graph


def _start_agent_end():
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "a", "component_type": "AGENT"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "ps_out", "node_id": "s", "direction": "out", "port_type": "out"},
        {"id": "pa_in", "node_id": "a", "direction": "in", "port_type": "in"},
        {"id": "pa_out", "node_id": "a", "direction": "out", "port_type": "out"},
        {"id": "pe_in", "node_id": "e", "direction": "in", "port_type": "in"},
    ]
    edges = [
        {"source_port_id": "ps_out", "target_port_id": "pa_in", "kind": "MAINFLOW"},
        {"source_port_id": "pa_out", "target_port_id": "pe_in", "kind": "MAINFLOW"},
    ]
    return nodes, edges, ports


def test_empty_inputs_emit_empty_orchestration_error():
    report = validate_graph(nodes=[], edges=[], ports=[])
    assert not report.valid
    assert any(c.id.startswith("ORCH-EMPTY") for c in report.errors)


def test_missing_project_dir_emits_load_error(tmp_workspace):
    report = validate_orchestration(str(tmp_workspace))
    assert not report.valid
    assert any(c.id.startswith("ORCH-LOAD") for c in report.errors)


def test_simple_start_agent_end_passes():
    nodes, edges, ports = _start_agent_end()
    report = validate_graph(
        nodes=nodes,
        edges=edges,
        ports=ports,
    )
    # Some warnings/info are OK; just check no errors.
    assert report.valid, [c.message for c in report.errors]


def test_invalid_agent_to_policy_connection_errors():
    # AGENT cannot connect to POLICY (per known platform rule).
    nodes = [
        {"id": "a", "component_type": "AGENT"},
        {"id": "p", "component_type": "POLICY"},
    ]
    ports = [
        {"id": "pa_out", "node_id": "a", "direction": "out", "port_type": "out"},
        {"id": "pp_in", "node_id": "p", "direction": "in", "port_type": "in"},
    ]
    # INVOKE edge so the connection matrix applies (an AGENT invoking a POLICY is
    # invalid). MAINFLOW edges are not matrix-checked — mainflow lives in node
    # config on the platform, not the edge list.
    edges = [
        {"source_port_id": "pa_out", "target_port_id": "pp_in", "kind": "INVOKE"},
    ]
    report = validate_graph(
        nodes=nodes,
        edges=edges,
        ports=ports,
    )
    assert not report.valid
    assert any("cannot connect" in c.message.lower() for c in report.errors)


def test_report_has_runtime_observations_structure():
    nodes, edges, ports = _start_agent_end()
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert "calls" in report.runtime_observations
    assert "hosts" in report.runtime_observations
