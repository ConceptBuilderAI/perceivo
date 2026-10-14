"""Project directory loader for orchestration validation."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

import yaml

from perceivo._internal.paths import resolve_within_project
from perceivo.validator._paths import read_text_or_reason

from ._component_ports import apply_component_ports
from ._mainflow_translation import translate_mainflow_out_ports
from ._manifest_overlay import overlay_manifest_ports


def _read_orchestration(orch_file: Path) -> Optional[dict[str, Any]]:
    """Parse the top-level orchestration.yaml, returning None if unreadable or not YAML."""
    text, _ = read_text_or_reason(orch_file)
    if text is None:
        return None
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None


def orchestration_unreadable(project_dir: str) -> Optional[str]:
    """Why the project's orchestration.yaml cannot be read, if it exists and cannot."""
    orch_file = Path(project_dir) / "orchestration.yaml"
    return read_text_or_reason(orch_file)[1] if orch_file.exists() else None


def _build_component(node: dict[str, Any], path: Path) -> Optional[dict[str, Any]]:
    """Build a component dict from a node, or None if the node has no script."""
    script_name = node.get("script", "")
    if not script_name:
        return None

    manifest_name = node.get("manifest", "")
    # Graph-supplied paths are confined to the project, so a project naming
    # ``../../.ssh/id_rsa`` cannot make the validator read it.
    node_id = node.get("id") or node.get("name")
    script_path = resolve_within_project(path, script_name, field="script", node_id=node_id)
    # With no manifest named, export reads the one beside the resolved script.
    manifest_path = (
        resolve_within_project(path, manifest_name, field="manifest", node_id=node_id)
        if manifest_name
        else script_path.resolve().with_suffix(".yaml")
    )

    comp: dict[str, Any] = {
        "name": node.get("name", script_name),
        "component_type": node.get("component_type", ""),
        "script": "",
        "manifest_yaml": None,
        "manifest_unreadable": None,
        "script_unreadable": None,
    }
    if script_path.exists():
        script, comp["script_unreadable"] = read_text_or_reason(script_path)
        comp["script"] = script or ""
    if manifest_path.exists():
        comp["manifest_yaml"], comp["manifest_unreadable"] = read_text_or_reason(manifest_path)
    return comp


def _node_port_rows(ports: list[dict[str, Any]], node_id: str) -> Optional[list[dict[str, Any]]]:
    """Return the port rows the graph declares for *node_id*, shaped for the port rules.

    These stand in for the component's ports only where its bundled ports cannot
    be built (:func:`._component_ports.apply_component_ports`), so a component
    whose files are incomplete is still judged on the ports the graph lists.
    Where the graph declares no port for the node, None keeps the manifest as the
    only declaration there is.

    Only what the count rules read is carried: the stored ``direction`` is IN or
    OUT, and the graph's lower-case spelling of it is not a component finding.
    """
    rows = [
        {
            "key": p.get("key") or p.get("name"),
            "port_type": p.get("port_type"),
            "direction": str(p.get("direction") or "").upper() or None,
        }
        for p in ports
        if isinstance(p, dict) and str(p.get("node_id", "")) == node_id and not p.get("virtual")
    ]
    return rows or None


def _expand_config_mainflow(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Materialize ``config.mainflow_target_node_*`` hops as MAINFLOW edges.

    A node on the mainflow records its continuation in config, not as an edge
    (the platform's phantom-port model). The bundle reader already expands that
    into an edge from a synthesized ``virtual`` out port; the project loader must
    do the same, or a project that spells its mainflow the way the platform
    requires would load as a broken chain, with END unwired and no mainflow
    structure.

    Reuses the bundle reader's implementation so the two paths cannot drift.
    Imported lazily: ``perceivo.orchestration._builder`` pulls in the bundle
    writer, and importing it at module scope would tie the validator to that
    import cycle.
    """
    if not isinstance(nodes, list) or not isinstance(ports, list):
        return
    if not isinstance(edges, list):
        return
    if not any((node.get("config") or {}) for node in nodes if isinstance(node, dict)):
        return

    from perceivo.orchestration._builder._bundle_project import _add_mainflow_edges

    _add_mainflow_edges(nodes, ports, edges)


def _load_from_directory(project_dir: str) -> Optional[dict[str, Any]]:
    """Load an orchestration definition from a project directory.

    The directory holds ``orchestration.yaml`` (the graph: nodes, edges, or a node
    list) and ``*.py`` / ``*.yaml`` pairs (component scripts and manifests).
    """
    path = Path(project_dir)
    orch_file = path / "orchestration.yaml"
    if not orch_file.exists():
        return None

    orch = _read_orchestration(orch_file)
    if orch is None:
        return None

    nodes = orch.get("nodes", [])
    edges = orch.get("edges", [])
    ports = orch.get("ports", [])
    component_types: dict[str, str] = {}
    components: list[dict[str, Any]] = []
    component_by_node: dict[str, dict[str, Any]] = {}
    # The rows as written, before the manifest overlay rewrites them: publish
    # builds each component port from these.
    source_ports = copy.deepcopy(ports)

    for node in nodes:
        node_id = node.get("id", "")
        component_types[node_id] = node.get("component_type", "")

        comp = _build_component(node, path)
        # The manifest is the platform's source of truth for port schemas and
        # artifact types; the graph's copy can be stale. A node with no script
        # has no manifest, but its rows still need their direction.
        overlay_manifest_ports((comp["manifest_yaml"] or "") if comp else "", node_id, ports)
        if comp is None:
            continue

        comp["ports"] = _node_port_rows(ports, node_id)
        components.append(comp)
        component_by_node[str(node_id or "")] = comp

    # Judge an AGENT/TOOL ``out`` hop as publish sends it: as a config hop from
    # the node's in port, which carries the node's response contract.
    kept_ports, edges, translated = translate_mainflow_out_ports(nodes, ports, edges)
    bundled: dict[str, list[dict[str, Any]]] = {}
    if isinstance(ports, list):
        # The platform counts the ports of the component publish creates, which
        # include manifest ports no row lists.
        bundled = apply_component_ports(path, nodes, ports, source_ports, translated)
        for node_id, bundled_ports in bundled.items():
            if node_id in component_by_node:
                component_by_node[node_id]["ports"] = bundled_ports
    ports = kept_ports
    _expand_config_mainflow(nodes, ports, edges)

    return {
        "nodes": nodes,
        "edges": edges,
        "ports": ports,
        "component_types": component_types,
        "components": components,
        "bundled_ports": bundled,
    }
