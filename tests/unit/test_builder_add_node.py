"""T4: Orchestration.add_node — string-path mode + port registry."""

from __future__ import annotations

import pytest

from perceivo.orchestration._builder import Orchestration


def test_add_node_assigns_expected_default_ports():
    orch = Orchestration("t")
    n = orch.add_node("agent", "AGENT", script="a.py", manifest="a.yaml")
    port_names = {p["name"] for p in n.ports}
    assert {"input", "output"} <= port_names
    for p in n.ports:
        assert p["id"] == f"port_{n.id}_{p['name']}"
        assert p["node_id"] == n.id


def test_add_node_registers_ports_globally():
    orch = Orchestration("t")
    n = orch.add_node("agent", "AGENT", script="a.py", manifest="a.yaml")
    for p in n.ports:
        assert orch._port_registry[p["id"]] == n.id


def test_add_node_normalises_component_type_case():
    orch = Orchestration("t")
    n = orch.add_node("a", "tool", script="a.py", manifest="a.yaml")
    assert n.component_type == "TOOL"


def test_duplicate_node_id_rejected():
    orch = Orchestration("t")
    orch.add_node("agent", "AGENT", script="a.py", manifest="a.yaml")
    with pytest.raises(ValueError, match="already exists"):
        orch.add_node("agent", "AGENT", script="b.py", manifest="b.yaml")


def test_extra_ports_are_appended_with_ids():
    orch = Orchestration("t")
    n = orch.add_node(
        "fsm",
        "FSM",
        script="fsm.py",
        manifest="fsm.yaml",
        extra_ports=[{"name": "review_check", "port_type": "review"}],
    )
    extra = [p for p in n.ports if p["name"] == "review_check"]
    assert len(extra) == 1
    assert extra[0]["id"] == f"port_{n.id}_review_check"
    assert orch._port_registry[extra[0]["id"]] == n.id


def test_unknown_component_type_yields_no_default_ports():
    orch = Orchestration("t")
    n = orch.add_node("ghost", "WHATEVER", script="g.py", manifest="g.yaml")
    assert n.ports == []


def test_explicit_node_id_is_honoured():
    orch = Orchestration("t")
    n = orch.add_node(
        "agent",
        "AGENT",
        script="a.py",
        manifest="a.yaml",
        node_id="explicit_id",
    )
    assert n.id == "explicit_id"


def test_string_script_does_not_set_runtime_fields():
    orch = Orchestration("t")
    n = orch.add_node("agent", "AGENT", script="a.py", manifest="a.yaml")
    assert n.func is None
    assert n.script_source is None
    assert n.manifest_source is None
