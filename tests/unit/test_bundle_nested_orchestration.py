"""Nested-orchestration export from constructed local projects.

``_bundle/orchestration.py::orchestration_to_bundle`` exports every
``component_type == ORCHESTRATION`` node. Skipping one is not a partial export:
the node disappears from ``nodes`` while **its ports and edges stay**, so the
bundle declares ports whose ``node_ref`` names nothing. Re-materializing that
bundle and compiling it fails with

    RuntimeError: MAINFLOW edge source port 'port_N' resolves to node 'child',
    which is not a declared node.

so the corpus's only nested-orchestration proof
(``simple-embedded-orchestration``) cannot be round-tripped at all.

The tests below pin that failure — a dangling ``node_ref`` and the compile error
it causes — rather than restating the writer's structure.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from perceivo._bundle.orchestration import (
    NestedOrchestrationError,
    bundle_to_orchestration,
    orchestration_to_bundle,
)
from perceivo._bundle.schema import OrchestrationExport
from perceivo.runner.compiler import compile_project
from perceivo.types.errors import PerceivoError

# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


#: The external port the platform requires of every START and END component.
_EXTERNAL_PORT = {
    "START": ("external_in_port", {"port_name": "external_input", "port_type": "external_in"}),
    "END": ("external_out_port", {"port_name": "external_output", "port_type": "external_out"}),
}


def _component(project: Path, stem: str, ctype: str) -> None:
    (project / f"{stem}.py").write_bytes(f"# {stem}\n".encode())
    manifest = {"extension_type": ctype, "name": stem}
    if ctype in _EXTERNAL_PORT:
        section, port = _EXTERNAL_PORT[ctype]
        manifest[section] = port
    (project / f"{stem}.yaml").write_bytes(yaml.safe_dump(manifest).encode())


def _write_orchestration(project: Path, orch: dict) -> None:
    (project / "orchestration.yaml").write_bytes(
        yaml.safe_dump(orch, sort_keys=False, allow_unicode=True).encode()
    )


def _write_leaf(project: Path, name: str) -> Path:
    """A flat START -> END project, the thing a parent embeds."""
    project.mkdir(parents=True, exist_ok=True)
    _component(project, "start", "START")
    _component(project, "end", "END")
    _write_orchestration(
        project,
        {
            "name": name,
            "nodes": [
                {
                    "id": "start",
                    "component_type": "START",
                    "script": "start.py",
                    "manifest": "start.yaml",
                    "name": "Start",
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
                    "id": "p_end_in",
                    "node_id": "end",
                    "key": "input",
                    "name": "input",
                    "direction": "in",
                    "port_type": "in",
                },
            ],
            "edges": [
                {"source_port_id": "p_start_out", "target_port_id": "p_end_in", "kind": "MAINFLOW"},
            ],
        },
    )
    return project


def _write_parent(root: Path, *, child_ref: str | None = "nested/leaf") -> Path:
    """START -> <ORCHESTRATION child> -> END, the nested shape.

    *child_ref* is written verbatim as the node's ``project`` field; pass ``None``
    to omit it entirely.
    """
    project = root / "parent"
    project.mkdir(parents=True, exist_ok=True)
    _component(project, "start", "START")
    _component(project, "end", "END")
    child_node: dict = {"id": "child", "component_type": "ORCHESTRATION", "name": "Child"}
    if child_ref is not None:
        child_node["project"] = child_ref
    _write_orchestration(
        project,
        {
            "name": "parent-orch",
            "nodes": [
                {
                    "id": "start",
                    "component_type": "START",
                    "script": "start.py",
                    "manifest": "start.yaml",
                    "name": "Start",
                },
                child_node,
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
                    "id": "p_child_in",
                    "node_id": "child",
                    "key": "orchestration",
                    "name": "orchestration",
                    "direction": "in",
                    "port_type": "in",
                },
                {
                    "id": "p_child_out",
                    "node_id": "child",
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
                {
                    "source_port_id": "p_start_out",
                    "target_port_id": "p_child_in",
                    "kind": "MAINFLOW",
                },
                {"source_port_id": "p_child_out", "target_port_id": "p_end_in", "kind": "MAINFLOW"},
            ],
        },
    )
    return project


def _nested_parent(root: Path) -> Path:
    """A parent whose ``nested/leaf`` child actually exists on disk."""
    parent = _write_parent(root)
    _write_leaf(parent / "nested" / "leaf", "leaf-orch")
    return parent


def _dangling_port_refs(bundle: dict) -> list[str]:
    """Ports whose ``node_ref`` names no node in the bundle — the dangling-port corruption."""
    declared = {node["local_id"] for node in bundle["nodes"]}
    return [p["node_ref"] for p in bundle["ports"] if p["node_ref"] not in declared]


# --------------------------------------------------------------------------- #
# The nested node survives export instead of being dropped                    #
# --------------------------------------------------------------------------- #


def test_nested_node_is_exported_with_a_ref_into_the_orchestrations_map(tmp_path):
    bundle = orchestration_to_bundle(_nested_parent(tmp_path))
    OrchestrationExport.model_validate(bundle)

    nodes = {n["local_id"]: n for n in bundle["nodes"]}
    assert set(nodes) == {"start", "child", "end"}, "the ORCHESTRATION node was dropped"

    ref = nodes["child"]["orchestration_ref"]
    assert ref, "the nested node must carry the ref keying into 'orchestrations'"
    # The platform writes the same ref into both fields (see the corpus bundle's
    # node_1: component_ref == orchestration_ref == "orch_0").
    assert nodes["child"]["component_ref"] == ref
    assert ref in bundle["orchestrations"]
    # ...and the nested child is a bundle in its own right, not a stub.
    child = bundle["orchestrations"][ref]
    assert child["export_type"] == "orchestration"
    assert {n["local_id"] for n in child["nodes"]} == {"start", "end"}
    assert len(child["components"]) == 2
    # The child's own components do not leak into the parent's component map.
    assert set(bundle["components"]) == {"comp_0", "comp_1"}


def test_export_leaves_no_port_referencing_an_undeclared_node(tmp_path):
    # A dropped node whose two ports stay leaves 'child' as a node_ref with no
    # matching node.
    bundle = orchestration_to_bundle(_nested_parent(tmp_path))
    assert _dangling_port_refs(bundle) == []
    assert ("child", "orchestration") in {
        (p["node_ref"], p["component_port_key"]) for p in bundle["ports"]
    }


def test_both_mainflow_edges_survive_the_nested_hop(tmp_path):
    # ORCHESTRATION is not an AGENT/TOOL, so neither edge is translated into
    # config.mainflow_target_node_ref: both stay as real edges.
    bundle = orchestration_to_bundle(_nested_parent(tmp_path))
    ports = bundle["ports"]
    hops = {
        (ports[e["source_port_index"]]["node_ref"], ports[e["target_port_index"]]["node_ref"])
        for e in bundle["edges"]
        if e["edge_kind"] == "MAINFLOW"
    }
    assert hops == {("start", "child"), ("child", "end")}


def test_round_tripped_project_compiles_with_the_nested_node_resolved(tmp_path):
    # Ports that outlive the node make this raise RuntimeError("... resolves to
    # node 'child', which is not a declared node").
    bundle = orchestration_to_bundle(_nested_parent(tmp_path))
    restored = bundle_to_orchestration(bundle, tmp_path / "out")

    result = compile_project(str(restored))
    shells = result.shells_by_id
    assert set(shells) == {"start", "child", "end"}

    child = shells["child"]
    assert child.component_type == "ORCHESTRATION"
    assert child.project_path, "the nested project directory must resolve on disk"
    assert (Path(child.project_path) / "orchestration.yaml").exists()
    assert child.mainflow_successors == ["end"]
    assert shells["start"].mainflow_successors == ["child"]


def test_round_trip_is_stable_over_a_second_export(tmp_path):
    once = orchestration_to_bundle(_nested_parent(tmp_path))
    restored = bundle_to_orchestration(once, tmp_path / "out")
    twice = orchestration_to_bundle(restored)
    OrchestrationExport.model_validate(twice)

    assert _dangling_port_refs(twice) == []
    nested_nodes = [n for n in twice["nodes"] if n.get("orchestration_ref")]
    assert len(nested_nodes) == 1
    assert set(twice["orchestrations"]) == {nested_nodes[0]["orchestration_ref"]}


# --------------------------------------------------------------------------- #
# unresolvable nesting is loud, not silent                                     #
# --------------------------------------------------------------------------- #


def test_nested_node_without_a_project_field_raises(tmp_path):
    parent = _write_parent(tmp_path, child_ref=None)
    with pytest.raises(ValueError, match=r"'child'.*ORCHESTRATION.*'project'"):
        orchestration_to_bundle(parent)


def test_nested_node_pointing_at_a_missing_directory_raises(tmp_path):
    parent = _write_parent(tmp_path, child_ref="nested/absent")
    with pytest.raises(FileNotFoundError, match=r"orchestration\.yaml"):
        orchestration_to_bundle(parent)


def test_self_referential_nesting_raises_naming_the_cycle(tmp_path):
    # 'project: .' makes the parent its own child. Without the guard this
    # recurses to the interpreter limit and reports a RecursionError that names
    # no orchestration.
    parent = _write_parent(tmp_path, child_ref=".")
    with pytest.raises(ValueError, match="cycle"):
        orchestration_to_bundle(parent)


def test_the_nesting_error_is_catchable_from_both_roots(tmp_path):
    # Asserted in both directions: rooting it on PerceivoError without keeping
    # ValueError would break every `except ValueError` already written around
    # the writer, and keeping only ValueError leaves it escaping the one
    # `except PerceivoError` the SDK promises catches everything.
    assert issubclass(NestedOrchestrationError, PerceivoError)
    assert issubclass(NestedOrchestrationError, ValueError)
    parent = _write_parent(tmp_path, child_ref=None)
    with pytest.raises(PerceivoError):
        orchestration_to_bundle(parent)


def test_indirect_nesting_cycle_raises(tmp_path):
    parent = _write_parent(tmp_path)
    child = _write_leaf(parent / "nested" / "leaf", "leaf-orch")
    # point the grandchild back at the top-level parent
    data = yaml.safe_load((child / "orchestration.yaml").read_text(encoding="utf-8"))
    data["nodes"].append(
        {
            "id": "back",
            "component_type": "ORCHESTRATION",
            "name": "Back",
            "project": os.path.relpath(parent, child).replace("\\", "/"),
        }
    )
    _write_orchestration(child, data)

    with pytest.raises(ValueError, match="cycle"):
        orchestration_to_bundle(parent)


# --------------------------------------------------------------------------- #
# depth and flat-bundle fidelity                                               #
# --------------------------------------------------------------------------- #


def test_two_levels_of_nesting_are_both_emitted(tmp_path):
    parent = _write_parent(tmp_path)
    middle = _write_parent(parent / "nested")  # writes <parent>/nested/parent
    _write_leaf(middle / "nested" / "leaf", "leaf-orch")
    # re-point the top-level parent at the middle project
    data = yaml.safe_load((parent / "orchestration.yaml").read_text(encoding="utf-8"))
    for node in data["nodes"]:
        if node["id"] == "child":
            node["project"] = "nested/parent"
    _write_orchestration(parent, data)

    bundle = orchestration_to_bundle(parent)
    OrchestrationExport.model_validate(bundle)

    child = next(iter(bundle["orchestrations"].values()))
    grandchild = next(iter(child["orchestrations"].values()))
    assert {n["local_id"] for n in grandchild["nodes"]} == {"start", "end"}
    assert "orchestrations" not in grandchild, "a flat leaf must not carry the nesting key"


def test_a_flat_orchestration_gains_no_nesting_keys(tmp_path):
    # The platform omits both keys on a flat orchestration and all 27 flat
    # corpus bundles are written that way; emitting explicit nulls would make
    # every SDK-written bundle differ from the platform's.
    bundle = orchestration_to_bundle(_write_leaf(tmp_path / "leaf", "leaf-orch"))
    assert "orchestrations" not in bundle
    assert all("orchestration_ref" not in node for node in bundle["nodes"])
    # ...and the key is only omitted when absent, never when populated.
    nested = orchestration_to_bundle(_nested_parent(tmp_path))
    assert "orchestrations" in nested


def test_private_orchestration_reference_round_trips_in_private_source(tmp_path):
    project = _write_leaf(tmp_path / "private-ref", "private-ref")
    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    data["nodes"].append(
        {
            "id": "shared-child",
            "component_type": "ORCHESTRATION",
            "component_ref": "shared-child-ref",
            "orchestration_asset_id": "9f9f9f9f",
            "name": "Shared child",
        }
    )
    _write_orchestration(project, data)

    bundle = orchestration_to_bundle(project)
    child = next(node for node in bundle["nodes"] if node["local_id"] == "shared-child")

    assert child["component_ref"] == "shared-child-ref"
    assert child["orchestration_asset_id"] == "9f9f9f9f"
    assert "orchestration_ref" not in child


def test_sibling_nested_orchestrations_get_distinct_refs(tmp_path):
    parent = _write_parent(tmp_path)
    _write_leaf(parent / "nested" / "leaf", "leaf-orch")
    _write_leaf(parent / "nested" / "other", "other-orch")
    data = yaml.safe_load((parent / "orchestration.yaml").read_text(encoding="utf-8"))
    data["nodes"].append(
        {
            "id": "child2",
            "component_type": "ORCHESTRATION",
            "name": "Child2",
            "project": "nested/other",
        }
    )
    data["ports"].append(
        {
            "id": "p_child2_in",
            "node_id": "child2",
            "key": "orchestration",
            "name": "orchestration",
            "direction": "in",
            "port_type": "in",
        }
    )
    _write_orchestration(parent, data)

    bundle = orchestration_to_bundle(parent)
    refs = {
        n["local_id"]: n["orchestration_ref"] for n in bundle["nodes"] if n.get("orchestration_ref")
    }
    assert set(refs) == {"child", "child2"}
    assert len(set(refs.values())) == 2, "each nested node needs its own ref"
    assert set(bundle["orchestrations"]) == set(refs.values())
    assert _dangling_port_refs(bundle) == []
