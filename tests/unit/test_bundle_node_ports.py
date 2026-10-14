"""Node-declared ports must survive export into the component definition.

``orchestration.yaml`` lets a node declare ports the component manifest does not —
that is what ``Orchestration.add_node(extra_ports=...)`` writes, and it is the only
way an AGENT gets the ``tool``/``policy``/``review`` port an INVOKE edge leaves
through. The bundle wire format has nowhere to put a node-level ``port_type``:
``PortRefExportData`` is ``(node_ref, component_port_key, component_port_direction,
alias, effective_contract)``, and the platform importer resolves that triple against
the *component's* ports (``_orchestration_import_payload._resolve_port_ref`` ->
``_orchestration_import._load_port_key_map``). A port the component does not declare
therefore either fails to resolve at all, or — once resolved by direction alone —
reads as a plain ``out`` port and trips the layered-source rule
(``_edge_validation_helpers._layered_source_error``).

So the port has to be ON the exported component, which makes the component's port
set part of its identity: two nodes sharing one ``(script, manifest)`` pair but
declaring different invoke ports must NOT be deduplicated onto one definition, or
each inherits the other's ports as canvas-visible phantoms.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._bundle.schema import OrchestrationExport

_AGENT_MANIFEST = {
    "extension_type": "AGENT",
    "name": "agent",
    "input_ports": [{"port_name": "input", "port_type": "in"}],
}


def _write_files(proj: Path, stems: dict[str, dict]) -> None:
    for stem, manifest in stems.items():
        (proj / f"{stem}.py").write_text(f"# {stem}\n", encoding="utf-8")
        (proj / f"{stem}.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


def _port(pid: str, node: str, key: str, port_type: str, **extra) -> dict:
    return {"id": pid, "node_id": node, "key": key, "name": key, "port_type": port_type, **extra}


def _comp_ports(bundle: dict, ref: str) -> dict[str, tuple[str, str]]:
    """``{key: (port_type, direction)}`` for one exported component definition."""
    return {p["key"]: (p["port_type"], p["direction"]) for p in bundle["components"][ref]["ports"]}


def _ref_of(bundle: dict, node_id: str) -> str:
    ref = next(n["component_ref"] for n in bundle["nodes"] if n["local_id"] == node_id)
    assert isinstance(ref, str), f"node {node_id!r} has a non-string component_ref: {ref!r}"
    return ref


# --------------------------------------------------------------------------- #
# The defect: a node's INVOKE port never reached the component definition      #
# --------------------------------------------------------------------------- #


def _write_invoke_project(root: Path) -> Path:
    """START -> AGENT -> END with the AGENT invoking a TOOL through a ``tool`` port."""
    proj = root / "proj"
    proj.mkdir()
    _write_files(
        proj,
        {
            "start": {
                "extension_type": "START",
                "name": "start",
                "external_in_port": {"port_name": "trigger"},
                "ports": [{"key": "output", "port_type": "out"}],
            },
            "agent": _AGENT_MANIFEST,
            "tool": {
                "extension_type": "TOOL",
                "name": "tool",
                "input_ports": [{"port_name": "input", "port_type": "in"}],
            },
            "end": {
                "extension_type": "END",
                "name": "end",
                "external_out_port": {"port_name": "result"},
                "ports": [{"key": "input", "port_type": "in"}],
            },
        },
    )
    orch = {
        "name": "invoke-demo",
        "nodes": [
            {
                "id": "start",
                "component_type": "START",
                "script": "start.py",
                "manifest": "start.yaml",
            },
            {
                "id": "agent",
                "component_type": "AGENT",
                "script": "agent.py",
                "manifest": "agent.yaml",
            },
            {"id": "tool", "component_type": "TOOL", "script": "tool.py", "manifest": "tool.yaml"},
            {"id": "end", "component_type": "END", "script": "end.py", "manifest": "end.yaml"},
        ],
        "ports": [
            _port("p_start_out", "start", "output", "out"),
            _port("p_agent_in", "agent", "input", "in"),
            _port("p_agent_out", "agent", "output", "out"),
            _port("p_agent_invoke", "agent", "invoke_http", "tool"),
            _port("p_tool_in", "tool", "input", "in"),
            _port("p_end_in", "end", "input", "in"),
        ],
        "edges": [
            {"source_port_id": "p_start_out", "target_port_id": "p_agent_in", "kind": "MAINFLOW"},
            {"source_port_id": "p_agent_out", "target_port_id": "p_end_in", "kind": "MAINFLOW"},
            {"source_port_id": "p_agent_invoke", "target_port_id": "p_tool_in", "kind": "INVOKE"},
        ],
    }
    (proj / "orchestration.yaml").write_text(
        yaml.safe_dump(orch, sort_keys=False), encoding="utf-8"
    )
    return proj


def test_node_invoke_port_reaches_the_exported_component(tmp_path):
    bundle = orchestration_to_bundle(_write_invoke_project(tmp_path))
    OrchestrationExport.model_validate(bundle)

    ports = _comp_ports(bundle, _ref_of(bundle, "agent"))
    assert "invoke_http" in ports, (
        "the node's INVOKE port never reached the component definition, so the "
        f"platform resolves it as a plain OUT port; exported: {sorted(ports)}"
    )
    assert ports["invoke_http"] == ("tool", "OUT")


def test_every_exported_port_ref_resolves_against_its_component(tmp_path):
    """The importer's ``(comp_ref, key, direction)`` lookup must find every port ref."""
    bundle = orchestration_to_bundle(_write_invoke_project(tmp_path))

    declared = {
        (ref, p["key"], p["direction"])
        for ref, comp in bundle["components"].items()
        for p in comp["ports"]
    }
    unresolved = [
        (p["node_ref"], p["component_port_key"], p["component_port_direction"])
        for p in bundle["ports"]
        if (_ref_of(bundle, p["node_ref"]), p["component_port_key"], p["component_port_direction"])
        not in declared
    ]
    assert not unresolved, f"port refs the importer cannot resolve: {unresolved}"


def test_tool_port_is_flagged_as_a_tool_call(tmp_path):
    bundle = orchestration_to_bundle(_write_invoke_project(tmp_path))
    invoke = next(
        p
        for p in bundle["components"][_ref_of(bundle, "agent")]["ports"]
        if p["key"] == "invoke_http"
    )
    assert invoke["tool_call"] is True


def test_manifest_declared_ports_are_not_duplicated(tmp_path):
    """A node port the manifest already declares must not be emitted twice."""
    bundle = orchestration_to_bundle(_write_invoke_project(tmp_path))
    keys = [p["key"] for p in bundle["components"][_ref_of(bundle, "agent")]["ports"]]
    assert len(keys) == len(set(keys)), keys


# --------------------------------------------------------------------------- #
# The hazard dedup must not introduce: cross-contamination on the dedup key   #
# --------------------------------------------------------------------------- #


def _write_shared_component_project(root: Path, *, same_ports: bool) -> Path:
    """Two AGENT nodes on ONE (script, manifest) pair, each invoking its own TOOL.

    With ``same_ports`` the two nodes declare the identical invoke port, which is
    the case that must still deduplicate onto a single component definition.
    """
    proj = root / "proj"
    proj.mkdir()
    _write_files(
        proj,
        {
            "agent": _AGENT_MANIFEST,
            "tool_a": {
                "extension_type": "TOOL",
                "name": "tool_a",
                "input_ports": [{"port_name": "input", "port_type": "in"}],
            },
            "tool_b": {
                "extension_type": "TOOL",
                "name": "tool_b",
                "input_ports": [{"port_name": "input", "port_type": "in"}],
            },
        },
    )
    b_port = "invoke_a" if same_ports else "invoke_b"
    orch = {
        "name": "shared-component",
        "nodes": [
            {
                "id": "agent_a",
                "component_type": "AGENT",
                "script": "agent.py",
                "manifest": "agent.yaml",
            },
            {
                "id": "agent_b",
                "component_type": "AGENT",
                "script": "agent.py",
                "manifest": "agent.yaml",
            },
            {
                "id": "tool_a",
                "component_type": "TOOL",
                "script": "tool_a.py",
                "manifest": "tool_a.yaml",
            },
            {
                "id": "tool_b",
                "component_type": "TOOL",
                "script": "tool_b.py",
                "manifest": "tool_b.yaml",
            },
        ],
        "ports": [
            _port("p_a_in", "agent_a", "input", "in"),
            _port("p_a_invoke", "agent_a", "invoke_a", "tool"),
            _port("p_b_in", "agent_b", "input", "in"),
            _port("p_b_invoke", "agent_b", b_port, "tool"),
            _port("p_ta_in", "tool_a", "input", "in"),
            _port("p_tb_in", "tool_b", "input", "in"),
        ],
        "edges": [
            {"source_port_id": "p_a_invoke", "target_port_id": "p_ta_in", "kind": "INVOKE"},
            {"source_port_id": "p_b_invoke", "target_port_id": "p_tb_in", "kind": "INVOKE"},
        ],
    }
    (proj / "orchestration.yaml").write_text(
        yaml.safe_dump(orch, sort_keys=False), encoding="utf-8"
    )
    return proj


def test_shared_component_with_different_ports_does_not_cross_contaminate(tmp_path):
    bundle = orchestration_to_bundle(_write_shared_component_project(tmp_path, same_ports=False))
    OrchestrationExport.model_validate(bundle)

    ref_a, ref_b = _ref_of(bundle, "agent_a"), _ref_of(bundle, "agent_b")
    assert ref_a != ref_b, "two nodes declaring different port sets shared one component definition"
    ports_a, ports_b = _comp_ports(bundle, ref_a), _comp_ports(bundle, ref_b)
    assert ports_a["invoke_a"] == ("tool", "OUT")
    assert ports_b["invoke_b"] == ("tool", "OUT")
    assert "invoke_b" not in ports_a, f"agent_a inherited agent_b's ports: {ports_a}"
    assert "invoke_a" not in ports_b, f"agent_b inherited agent_a's ports: {ports_b}"
    # Both definitions still carry the manifest's own port.
    assert ports_a["input"] == ports_b["input"] == ("in", "IN")


def test_shared_component_with_identical_ports_still_deduplicates(tmp_path):
    """Splitting on the port set must not split nodes that genuinely agree."""
    bundle = orchestration_to_bundle(_write_shared_component_project(tmp_path, same_ports=True))
    assert _ref_of(bundle, "agent_a") == _ref_of(bundle, "agent_b")
    assert len(bundle["components"]) == 3  # one shared agent + two tools


def test_shared_component_port_sets_are_order_insensitive(tmp_path):
    """Same ports declared in a different order must still be one component."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_files(proj, {"agent": _AGENT_MANIFEST})
    orch = {
        "name": "order",
        "nodes": [
            {"id": "a", "component_type": "AGENT", "script": "agent.py", "manifest": "agent.yaml"},
            {"id": "b", "component_type": "AGENT", "script": "agent.py", "manifest": "agent.yaml"},
        ],
        "ports": [
            _port("p_a1", "a", "x", "tool"),
            _port("p_a2", "a", "y", "policy"),
            _port("p_b1", "b", "y", "policy"),
            _port("p_b2", "b", "x", "tool"),
        ],
        "edges": [],
    }
    (proj / "orchestration.yaml").write_text(
        yaml.safe_dump(orch, sort_keys=False), encoding="utf-8"
    )
    bundle = orchestration_to_bundle(proj)
    assert _ref_of(bundle, "a") == _ref_of(bundle, "b")
    assert _comp_ports(bundle, _ref_of(bundle, "a"))["y"] == ("policy", "OUT")


def test_mainflow_converted_out_port_is_not_added_to_the_component(tmp_path):
    """The out-port the mainflow translation drops must not reappear as a phantom."""
    bundle = orchestration_to_bundle(_write_invoke_project(tmp_path))
    ports = _comp_ports(bundle, _ref_of(bundle, "agent"))
    assert "output" not in ports, (
        "the AGENT 'out' port is translated into config.mainflow_target_node_ref "
        f"and must not be declared on the component: {sorted(ports)}"
    )
