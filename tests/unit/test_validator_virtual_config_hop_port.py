"""A config-hop virtual port carries no contract and must not mask the in-port."""

from __future__ import annotations

from perceivo.validator._orchestration._mainflow_dataflow import _downstream_contract_port

_IN = {"node_id": "fsm", "direction": "in", "port_type": "in", "response_data_schema": {"a": 1}}
_VIRTUAL_OUT = {"node_id": "fsm", "direction": "out", "port_type": "out", "virtual": True}
_REAL_OUT = {"node_id": "fsm", "direction": "out", "port_type": "out"}


def test_virtual_out_port_is_skipped_for_the_in_port():
    assert _downstream_contract_port("fsm", [_VIRTUAL_OUT, _IN]) is _IN


def test_real_out_port_still_wins():
    assert _downstream_contract_port("fsm", [_IN, _VIRTUAL_OUT, _REAL_OUT]) is _REAL_OUT


def test_only_a_virtual_port_yields_nothing():
    assert _downstream_contract_port("fsm", [_VIRTUAL_OUT]) is None


# --------------------------------------------------------------------------- #
# artifact types across a config hop                                           #
# --------------------------------------------------------------------------- #

from perceivo.validator._orchestration._main import validate_graph


def _config_hop_graph(target_expects: str) -> dict:
    """a -(config hop, virtual out-port)-> b, where a produces a TEXT artifact."""
    ports = [
        {
            "id": "a_in",
            "node_id": "a",
            "name": "input",
            "direction": "in",
            "port_type": "in",
            "artifact_type_input": "text",
            "artifact_type_output": "text",
        },
        {
            "id": "virtual_a_out",
            "node_id": "a",
            "name": "output",
            "key": "output",
            "direction": "out",
            "port_type": "out",
            "virtual": True,
        },
        {
            "id": "b_in",
            "node_id": "b",
            "name": "input",
            "direction": "in",
            "port_type": "in",
            "artifact_type_input": target_expects,
        },
    ]
    return {
        "nodes": [
            {"id": "a", "component_type": "AGENT", "config": {"mainflow_target_node_ref": "b"}},
            {"id": "b", "component_type": "AGENT"},
        ],
        "ports": ports,
        "edges": [
            {"source_port_id": "virtual_a_out", "target_port_id": "b_in", "kind": "MAINFLOW"}
        ],
    }


def _artifact_mismatches(target_expects: str) -> list:
    report = validate_graph(**_config_hop_graph(target_expects))
    return [c for c in report.checks if c.title == "Artifact type mismatch"]


def test_artifact_mismatch_across_a_config_hop_fails():
    mismatches = _artifact_mismatches("json")
    assert len(mismatches) == 1
    assert "source produces 'text', target expects 'json'" in mismatches[0].message
    assert mismatches[0].severity == "error"


def test_matching_artifact_across_a_config_hop_passes():
    assert _artifact_mismatches("text") == []
