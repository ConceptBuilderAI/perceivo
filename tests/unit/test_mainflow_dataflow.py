from perceivo.validator._orchestration._main import validate_graph

"""Regression coverage for mainflow passthrough propagation."""

from perceivo.validator._orchestration._mainflow_dataflow import (
    compute_available_response_fields,
)


def _chain(middle_response=None, **metadata):
    nodes = [
        {"id": "start", "component_type": "START"},
        {"id": "middle", "component_type": "AGENT"},
        {"id": "end", "component_type": "END"},
    ]
    ports = [
        {
            "id": "start_out",
            "node_id": "start",
            "direction": "out",
            "port_type": "out",
            "response_data_schema": {"origin": {"type": "string"}},
        },
        {
            "id": "middle_in",
            "node_id": "middle",
            "direction": "in",
            "port_type": "in",
            "request_data_schema": {"origin": {"type": "string"}},
        },
        {
            "id": "middle_out",
            "node_id": "middle",
            "direction": "out",
            "port_type": "out",
            "response_data_schema": middle_response or {},
            **metadata,
        },
        {
            "id": "end_in",
            "node_id": "end",
            "direction": "in",
            "port_type": "in",
            "request_data_schema": {"origin": {"type": "string"}},
        },
    ]
    edges = [
        {
            "source_port_id": "start_out",
            "target_port_id": "middle_in",
            "kind": "MAINFLOW",
        },
        {
            "source_port_id": "middle_out",
            "target_port_id": "end_in",
            "kind": "MAINFLOW",
        },
    ]
    return nodes, edges, ports


def _contract_messages(report):
    return [check.message for check in report.errors if check.category == "contract"]


def test_valid_passthrough_chain_is_accepted():
    nodes, edges, ports = _chain(response_data_passthrough="request_data")
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert report.valid, _contract_messages(report)


def test_missing_passthrough_declaration_still_rejects_chain():
    nodes, edges, ports = _chain()
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert any("Required field 'origin'" in error for error in _contract_messages(report))


def test_passthrough_exception_removes_upstream_field():
    nodes, edges, ports = _chain(
        response_data_passthrough="request_data",
        response_data_passthrough_except=["origin"],
    )
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert any("Required field 'origin'" in error for error in _contract_messages(report))


def test_declared_response_wins_over_passthrough_field_type():
    nodes, edges, ports = _chain(
        {"origin": {"type": "integer"}},
        response_data_passthrough="request_data",
    )
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert any("type mismatch" in error for error in _contract_messages(report))


def test_string_passthrough_exceptions_are_supported():
    nodes, edges, ports = _chain(
        response_data_passthrough="request_data",
        response_data_passthrough_except="other, origin",
    )
    provided = compute_available_response_fields(nodes, ports, edges)
    assert provided["middle"] == {}


def test_disconnected_chains_are_computed_independently():
    nodes, edges, ports = _chain(response_data_passthrough="request_data")
    nodes.extend(
        [
            {"id": "start2", "component_type": "START"},
            {"id": "end2", "component_type": "END"},
        ]
    )
    ports.extend(
        [
            {
                "id": "start2_out",
                "node_id": "start2",
                "direction": "out",
                "port_type": "out",
                "response_data_schema": {"second": {"type": "integer"}},
            },
            {
                "id": "end2_in",
                "node_id": "end2",
                "direction": "in",
                "port_type": "in",
            },
        ]
    )
    edges.append(
        {
            "source_port_id": "start2_out",
            "target_port_id": "end2_in",
            "kind": "MAINFLOW",
        }
    )
    provided = compute_available_response_fields(nodes, ports, edges)
    assert provided["middle"]["origin"]["type"] == "string"
    assert provided["start2"]["second"]["type"] == "integer"


def test_cycle_without_head_is_bounded_and_invents_no_fields():
    nodes = [{"id": "a"}, {"id": "b"}]
    ports = [
        {"id": "a_out", "node_id": "a", "direction": "out", "port_type": "out"},
        {"id": "a_in", "node_id": "a", "direction": "in", "port_type": "in"},
        {"id": "b_out", "node_id": "b", "direction": "out", "port_type": "out"},
        {"id": "b_in", "node_id": "b", "direction": "in", "port_type": "in"},
    ]
    edges = [
        {"source_port_id": "a_out", "target_port_id": "b_in", "kind": "MAINFLOW"},
        {"source_port_id": "b_out", "target_port_id": "a_in", "kind": "MAINFLOW"},
    ]
    assert compute_available_response_fields(nodes, ports, edges) == {}


def test_invoke_contract_behavior_is_unchanged():
    nodes, edges, ports = _chain(response_data_passthrough="request_data")
    edges.append(
        {
            "source_port_id": "middle_out",
            "target_port_id": "end_in",
            "kind": "INVOKE",
        }
    )
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert any("source request_data_schema" in error for error in _contract_messages(report))
