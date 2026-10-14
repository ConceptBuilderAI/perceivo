"""T4: Orchestration serialization (to_yaml / to_dict / render_default_manifest)."""

from __future__ import annotations

import yaml

from perceivo.orchestration._builder import Orchestration
from perceivo.orchestration._builder._serialization import render_default_manifest


def test_to_yaml_includes_name_description_nodes_edges_ports():
    orch = Orchestration("flow", description="hello")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    b = orch.add_node("b", "AGENT", script="b.py", manifest="b.yaml")
    orch.add_edge(a, b)

    parsed = yaml.safe_load(orch.to_yaml())
    assert parsed["name"] == "flow"
    assert parsed["description"] == "hello"
    assert {n["id"] for n in parsed["nodes"]} == {a.id, b.id}
    assert len(parsed["edges"]) == 1
    assert len(parsed["ports"]) == 4  # 2 ports per AGENT


def test_to_yaml_omits_optional_script_manifest_when_missing():
    orch = Orchestration("flow")
    orch.add_node("a", "AGENT")  # no script/manifest
    parsed = yaml.safe_load(orch.to_yaml())
    node = parsed["nodes"][0]
    assert "script" not in node
    assert "manifest" not in node


def test_to_yaml_emits_base_schema_when_present():
    orch = Orchestration("flow")
    orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    parsed = yaml.safe_load(orch.to_yaml())
    port_names_with_schema = {p["name"] for p in parsed["ports"] if "base_schema" in p}
    assert {"input", "output"} <= port_names_with_schema


def test_to_dict_strips_runtime_only_fields():
    orch = Orchestration("flow")
    orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    d = orch.to_dict()
    node = d["nodes"][0]
    assert "func" not in node
    assert "script_source" not in node
    assert "manifest_source" not in node


def test_to_dict_preserves_edge_data():
    orch = Orchestration("flow")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    b = orch.add_node("b", "AGENT", script="b.py", manifest="b.yaml")
    edge = orch.add_edge(a, b)
    d = orch.to_dict()
    assert d["edges"][0]["source_port_id"] == edge.source_port_id
    assert d["edges"][0]["target_port_id"] == edge.target_port_id
    assert d["edges"][0]["kind"] == "MAINFLOW"


def test_render_default_manifest_maps_in_external_in_to_direction_in():
    ports = [
        {"name": "ext", "port_type": "external_in"},
        {"name": "i", "port_type": "in"},
        {"name": "o", "port_type": "out"},
        {"name": "ext_out", "port_type": "external_out"},
        {"name": "p", "port_type": "policy"},
    ]
    out = render_default_manifest("c", "AGENT", ports)
    parsed = yaml.safe_load(out)
    dirs = {p["key"]: p["direction"] for p in parsed["ports"]}
    assert dirs == {
        "ext": "in",
        "i": "in",
        "o": "out",
        "ext_out": "out",
        "p": "policy",
    }


def test_render_default_manifest_sets_required_fields():
    out = render_default_manifest("nm", "TOOL", [])
    parsed = yaml.safe_load(out)
    assert parsed["name"] == "nm"
    assert parsed["type"] == "TOOL"
    assert parsed["required_secrets"] == []
    assert parsed["input_parameters"] == []
