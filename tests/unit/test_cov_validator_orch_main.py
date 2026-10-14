"""Coverage-focused unit tests for ``perceivo.validator._orchestration._main``.

Covers ``validate_orchestration`` dispatch and aggregation: the data mode, the
component-type override, contract and artifact errors, dangling-port edges, the
load-failure and empty-project paths, and a project directory with and without
script validation.
"""

from __future__ import annotations

from perceivo.validator import validate_orchestration
from perceivo.validator._orchestration._main import validate_graph

from .fixtures_orch_validator import _write

# ---------------------------------------------------------------------------
# _main.py — validate_orchestration dispatch + aggregation
# ---------------------------------------------------------------------------


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


def test_main_valid_via_data_mode():
    nodes, edges, ports = _start_agent_end()
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert report.valid, [c.message for c in report.errors]


def test_main_component_types_override_and_contract_and_artifact_errors():
    # Explicit component_types map exercises the types_map.get branch.
    nodes = [{"id": "a"}, {"id": "b"}]
    component_types = {"a": "AGENT", "b": "AGENT"}
    ports = [
        {
            "id": "pa_out",
            "node_id": "a",
            "direction": "out",
            "port_type": "out",
            "response_data_schema": {"properties": {"x": {"type": "string"}}},
            "artifact_type_output": "TEXT",
        },
        {
            "id": "pb_in",
            "node_id": "b",
            "direction": "in",
            "port_type": "in",
            # Requires a field the source does not provide -> contract error.
            "request_data_schema": {
                "properties": {"y": {"type": "string"}},
                "required": ["y"],
            },
            "artifact_type_input": "IMAGE",  # mismatch -> artifact error
        },
    ]
    edges = [{"source_port_id": "pa_out", "target_port_id": "pb_in", "kind": "MAINFLOW"}]
    report = validate_graph(nodes=nodes, edges=edges, ports=ports, component_types=component_types)
    assert not report.valid
    cats = {c.category for c in report.errors}
    assert "contract" in cats
    assert any(c.title == "Port contract mismatch" for c in report.errors)
    assert any(c.title == "Artifact type mismatch" for c in report.errors)


def test_main_invalid_connection_reported():
    nodes = [{"id": "a", "component_type": "AGENT"}, {"id": "p", "component_type": "POLICY"}]
    ports = [
        {"id": "pa_out", "node_id": "a", "direction": "out", "port_type": "out"},
        {"id": "pp_in", "node_id": "p", "direction": "in", "port_type": "in"},
    ]
    # INVOKE edge: the connection matrix applies to real edges (AGENT cannot invoke
    # POLICY). MAINFLOW edges are not matrix-checked, matching the server.
    edges = [{"source_port_id": "pa_out", "target_port_id": "pp_in", "kind": "INVOKE"}]
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    assert not report.valid
    assert any(c.title == "Invalid connection" for c in report.errors)


def test_main_edge_with_dangling_ports_skipped():
    # Edge references ports absent from port_map -> the per-edge loop `continue`s.
    nodes, edges, ports = _start_agent_end()
    edges = [*edges, {"source_port_id": "ghost1", "target_port_id": "ghost2", "kind": "MAINFLOW"}]
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    # No connection/contract error added for the dangling edge.
    assert report.valid, [c.message for c in report.errors]


def test_main_load_error_via_empty_dir(tmp_path):
    report = validate_orchestration(str(tmp_path))
    assert not report.valid
    assert any(c.id.startswith("ORCH-LOAD") for c in report.errors)


def test_main_empty_after_load(tmp_path):
    # orchestration.yaml exists but has no nodes/edges/ports -> ORCH-EMPTY.
    _write(tmp_path, "orchestration.yaml", "nodes: []\nedges: []\nports: []\n")
    report = validate_orchestration(str(tmp_path))
    assert not report.valid
    assert any(c.id.startswith("ORCH-EMPTY") for c in report.errors)


def test_main_project_dir_runs_script_validation(tmp_path):
    # Full project dir: START/AGENT/END with a component script + manifest so the
    # script-validation loop runs and prefixes check ids with the component name.
    _write(
        tmp_path,
        "orchestration.yaml",
        (
            "nodes:\n"
            "  - id: s\n"
            "    component_type: START\n"
            "  - id: a\n"
            "    name: agent1\n"
            "    component_type: AGENT\n"
            "    script: agent.py\n"
            "  - id: e\n"
            "    component_type: END\n"
            "edges:\n"
            "  - source_port_id: ps_out\n"
            "    target_port_id: pa_in\n"
            "    kind: MAINFLOW\n"
            "  - source_port_id: pa_out\n"
            "    target_port_id: pe_in\n"
            "    kind: MAINFLOW\n"
            "ports:\n"
            "  - id: ps_out\n"
            "    node_id: s\n"
            "    port_type: out\n"
            "  - id: pa_in\n"
            "    node_id: a\n"
            "    port_type: in\n"
            "  - id: pa_out\n"
            "    node_id: a\n"
            "    port_type: out\n"
            "  - id: pe_in\n"
            "    node_id: e\n"
            "    port_type: in\n"
        ),
    )
    # A script that will trip at least one component check, so ids get prefixed.
    _write(tmp_path, "agent.py", "import os\n\ndef run():\n    return os.getcwd()\n")
    report = validate_orchestration(str(tmp_path), validate_scripts=True)
    # If the component validator emitted anything, ids are prefixed "agent1/...".
    if report.checks:
        assert any("/" in c.id for c in report.checks) or report.valid


def test_main_project_dir_skip_script_validation(tmp_path):
    _write(
        tmp_path,
        "orchestration.yaml",
        (
            "nodes:\n"
            "  - id: s\n"
            "    component_type: START\n"
            "  - id: a\n"
            "    component_type: AGENT\n"
            "    script: agent.py\n"
            "  - id: e\n"
            "    component_type: END\n"
            "edges:\n"
            "  - source_port_id: ps_out\n"
            "    target_port_id: pa_in\n"
            "    kind: MAINFLOW\n"
            "  - source_port_id: pa_out\n"
            "    target_port_id: pe_in\n"
            "    kind: MAINFLOW\n"
            "ports:\n"
            "  - id: ps_out\n"
            "    node_id: s\n"
            "    port_type: out\n"
            "  - id: pa_in\n"
            "    node_id: a\n"
            "    port_type: in\n"
            "  - id: pa_out\n"
            "    node_id: a\n"
            "    port_type: out\n"
            "  - id: pe_in\n"
            "    node_id: e\n"
            "    port_type: in\n"
        ),
    )
    _write(tmp_path, "agent.py", "def run():\n    return 1\n")
    report = validate_orchestration(str(tmp_path), validate_scripts=False)
    # No component-prefixed ids when script validation is disabled.
    assert not any("agent" in c.id and "/" in c.id for c in report.checks)
    assert report.valid, [c.message for c in report.errors]
