"""The SDK bundle converter on the local-project -> platform-import path.

Covers three behaviours of the ``publish`` path:

* ``port_type`` -> direction map. ``tool``/``policy``/``state``/``review``
  ports resolve to OUT, not to the IN default, in both
  ``component._port_direction`` and ``orchestration._direction_upper``.
* Sectioned ("FORMAT A") component manifests. A manifest that declares ports
  via ``input_ports`` / ``output_ports`` / ``tool_ports`` / ``policy_ports`` /
  ``state_ports`` / ``review_ports`` / ``external_in_port`` /
  ``external_out_port`` parses into explicit ``PortExportData`` entries, not
  an empty ports list.
* MAINFLOW edge out of an AGENT/TOOL ``out`` port. The server rejects
  it (``VAL-MAINFLOW-OUT-001``); it must be translated to the source node's
  ``config.mainflow_target_node_ref``, the edge dropped, and the orphaned
  out-port removed from the orchestration ports list. Round-trips must stay
  functionally stable (END remains mainflow-reachable).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from perceivo._bundle.component import (
    _port_direction,
    component_to_bundle,
    parse_manifest_ports,
)
from perceivo._bundle.orchestration import (
    _direction_upper,
    bundle_to_orchestration,
    orchestration_to_bundle,
)
from perceivo._bundle.schema import ComponentExport, OrchestrationExport

# --------------------------------------------------------------------------- #
# Full port_type -> direction map (both functions)                            #
# --------------------------------------------------------------------------- #

_OUT_TYPES = ["out", "tool", "policy", "state", "external_out", "review"]
_IN_TYPES = ["in", "external_in"]


@pytest.mark.parametrize("direction_fn", [_port_direction, _direction_upper])
@pytest.mark.parametrize("port_type", _OUT_TYPES)
def test_control_and_out_ports_map_to_out(direction_fn, port_type):
    # tool/policy/state/review are OUT, not the IN default.
    assert direction_fn({"port_type": port_type}) == "OUT"


@pytest.mark.parametrize("direction_fn", [_port_direction, _direction_upper])
@pytest.mark.parametrize("port_type", _IN_TYPES)
def test_in_ports_map_to_in(direction_fn, port_type):
    assert direction_fn({"port_type": port_type}) == "IN"


@pytest.mark.parametrize("direction_fn", [_port_direction, _direction_upper])
def test_unknown_port_type_falls_back_to_direction_field(direction_fn):
    assert direction_fn({"port_type": "weird", "direction": "out"}) == "OUT"
    assert direction_fn({"port_type": "weird", "direction": "in"}) == "IN"
    assert direction_fn({"port_type": "weird"}) == "IN"


# --------------------------------------------------------------------------- #
# Sectioned ("FORMAT A") manifest parsing                                     #
# --------------------------------------------------------------------------- #


def test_sectioned_manifest_parses_every_section():
    manifest = {
        "extension_type": "AGENT",
        "name": "svc",
        "external_in_port": {"port_name": "ext_in", "request_data": {"type": "object"}},
        "external_out_port": {"port_name": "ext_out", "response_data": {"type": "object"}},
        "input_ports": [{"port_name": "in1", "description": "an input"}],
        "output_ports": [{"port_name": "out1"}],
        "tool_ports": [{"port_name": "search"}],
        "policy_ports": [{"port_name": "guard"}],
        "state_ports": [{"port_name": "mem"}],
        "review_ports": [{"port_name": "hitl"}],
    }
    ports = parse_manifest_ports(manifest)
    by_key = {p.key: p for p in ports}
    assert set(by_key) == {"ext_in", "ext_out", "in1", "out1", "search", "guard", "mem", "hitl"}

    assert (by_key["ext_in"].direction, by_key["ext_in"].port_type) == ("IN", "external_in")
    assert (by_key["ext_out"].direction, by_key["ext_out"].port_type) == ("OUT", "external_out")
    assert (by_key["in1"].direction, by_key["in1"].port_type) == ("IN", "in")
    assert (by_key["out1"].direction, by_key["out1"].port_type) == ("OUT", "out")
    assert (by_key["search"].direction, by_key["search"].port_type) == ("OUT", "tool")
    assert (by_key["guard"].direction, by_key["guard"].port_type) == ("OUT", "policy")
    assert (by_key["mem"].direction, by_key["mem"].port_type) == ("OUT", "state")
    assert (by_key["hitl"].direction, by_key["hitl"].port_type) == ("OUT", "review")

    # Data schemas + labels carried through, tool_call flagged for tool ports.
    assert by_key["ext_in"].request_data_schema == {"type": "object"}
    assert by_key["ext_out"].response_data_schema == {"type": "object"}
    assert by_key["in1"].label == "an input"
    assert by_key["search"].tool_call is True
    assert by_key["out1"].tool_call is False


def test_typed_section_item_port_type_wins():
    # An output_ports item may override its default 'out' port_type.
    manifest = {"output_ports": [{"port_name": "review_out", "port_type": "review"}]}
    ports = parse_manifest_ports(manifest)
    assert len(ports) == 1
    assert ports[0].port_type == "review"
    assert ports[0].direction == "OUT"


def test_a_flat_manifest_ports_list_is_not_read():
    """The flat ``ports:`` form is gone: it exports no ports."""
    manifest = {
        "ports": [
            {"key": "input", "direction": "in", "port_type": "in"},
            {"key": "output", "direction": "out", "port_type": "out"},
        ]
    }
    assert parse_manifest_ports(manifest) == []


def test_component_to_bundle_sectioned_manifest_nonempty_ports(tmp_path):
    (tmp_path / "svc.py").write_text("# svc\n", encoding="utf-8")
    manifest = {
        "extension_type": "AGENT",
        "name": "svc",
        "external_in_port": {"port_name": "prompt"},
        "output_ports": [{"port_name": "result"}],
        "tool_ports": [{"port_name": "search"}],
    }
    (tmp_path / "svc.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    bundle = component_to_bundle(tmp_path / "svc.py")
    ComponentExport.model_validate(bundle)
    ports = bundle["component"]["ports"]
    assert ports, "BUG 3: sectioned manifest must not yield an empty ports list"
    by_key = {p["key"]: p for p in ports}
    assert (by_key["prompt"]["direction"], by_key["prompt"]["port_type"]) == ("IN", "external_in")
    assert (by_key["result"]["direction"], by_key["result"]["port_type"]) == ("OUT", "out")
    assert (by_key["search"]["direction"], by_key["search"]["port_type"]) == ("OUT", "tool")


# --------------------------------------------------------------------------- #
# MAINFLOW out-port -> config.mainflow_target_node_ref translation            #
# --------------------------------------------------------------------------- #


def _write_mainflow_project(root: Path, agent_type: str = "AGENT") -> Path:
    """Minimal START -> <agent_type> -> END project, both hops MAINFLOW."""
    proj = root / "proj"
    proj.mkdir()
    # START and END carry the external port the platform requires of them.
    terminal_ports = {
        "start": {"external_in_port": {"port_name": "trigger"}},
        "end": {"external_out_port": {"port_name": "result"}},
    }
    for stem, ctype in (("start", "START"), ("agent", agent_type), ("end", "END")):
        (proj / f"{stem}.py").write_text(f"# {stem}\n", encoding="utf-8")
        manifest = {"extension_type": ctype, "name": stem, **terminal_ports.get(stem, {})}
        (proj / f"{stem}.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    orch = {
        "name": "mainflow-demo",
        "nodes": [
            {
                "id": "start",
                "component_type": "START",
                "script": "start.py",
                "manifest": "start.yaml",
                "name": "Start",
            },
            {
                "id": "agent",
                "component_type": agent_type,
                "script": "agent.py",
                "manifest": "agent.yaml",
                "name": "Agent",
            },
            {
                "id": "end",
                "component_type": "END",
                "script": "end.py",
                "manifest": "end.yaml",
                "name": "End",
            },
        ],
        "ports": [
            {
                "id": "p_start_out",
                "node_id": "start",
                "key": "output",
                "name": "output",
                "direction": "out",
                "port_type": "out",
            },
            {
                "id": "p_agent_in",
                "node_id": "agent",
                "key": "input",
                "name": "input",
                "direction": "in",
                "port_type": "in",
            },
            {
                "id": "p_agent_out",
                "node_id": "agent",
                "key": "output",
                "name": "output",
                "direction": "out",
                "port_type": "out",
            },
            {
                "id": "p_end_in",
                "node_id": "end",
                "key": "input",
                "name": "input",
                "direction": "in",
                "port_type": "in",
            },
        ],
        "edges": [
            {"source_port_id": "p_start_out", "target_port_id": "p_agent_in", "kind": "MAINFLOW"},
            {"source_port_id": "p_agent_out", "target_port_id": "p_end_in", "kind": "MAINFLOW"},
        ],
    }
    (proj / "orchestration.yaml").write_text(
        yaml.safe_dump(orch, sort_keys=False), encoding="utf-8"
    )
    return proj


@pytest.mark.parametrize("agent_type", ["AGENT", "TOOL"])
def test_mainflow_out_edge_becomes_config_target(tmp_path, agent_type):
    proj = _write_mainflow_project(tmp_path, agent_type)
    bundle = orchestration_to_bundle(proj)
    OrchestrationExport.model_validate(bundle)

    nodes = {n["local_id"]: n for n in bundle["nodes"]}
    # (1) next hop encoded on the source node's config
    assert nodes["agent"]["config"]["mainflow_target_node_ref"] == "end"

    # (2) the agent 'out' port is dropped from the orchestration ports list,
    #     while the mainflow target's in-port survives
    port_keys = {(p["node_ref"], p["component_port_key"]) for p in bundle["ports"]}
    assert ("agent", "output") not in port_keys
    assert ("end", "input") in port_keys

    # (3) the agent->end edge is absent; only start->agent MAINFLOW remains
    ports = bundle["ports"]
    assert len(bundle["edges"]) == 1
    only = bundle["edges"][0]
    assert only["edge_kind"] == "MAINFLOW"
    assert ports[only["source_port_index"]]["node_ref"] == "start"
    assert ports[only["target_port_index"]]["node_ref"] == "agent"
    assert "end" not in {ports[e["target_port_index"]]["node_ref"] for e in bundle["edges"]}


@pytest.mark.parametrize("agent_type", ["AGENT", "TOOL"])
def test_mainflow_round_trip_keeps_end_reachable(tmp_path, agent_type):
    proj = _write_mainflow_project(tmp_path, agent_type)
    bundle = orchestration_to_bundle(proj)

    restored = bundle_to_orchestration(bundle, tmp_path / "out")
    data = yaml.safe_load((restored / "orchestration.yaml").read_text(encoding="utf-8"))
    ports = {p["id"]: p for p in data["ports"]}

    # the reader re-adds a MAINFLOW edge into the END node's in-port
    reachable = any(
        edge.get("kind") == "MAINFLOW" and ports[edge["target_port_id"]]["node_id"] == "end"
        for edge in data["edges"]
    )
    assert reachable, data["edges"]

    # and the agent node still carries the mainflow target config
    agent_node = next(n for n in data["nodes"] if n["id"] == "agent")
    assert agent_node["config"]["mainflow_target_node_ref"] == "end"

    # re-exporting the restored project stays stable (still translated, validates)
    again = orchestration_to_bundle(restored)
    OrchestrationExport.model_validate(again)
    again_nodes = {n["local_id"]: n for n in again["nodes"]}
    assert again_nodes["agent"]["config"]["mainflow_target_node_ref"] == "end"
