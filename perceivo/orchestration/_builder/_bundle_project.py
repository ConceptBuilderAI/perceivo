"""Materialize platform orchestration bundles as SDK projects.

**Empty component scripts.** A ``components`` entry whose ``script`` is blank
is a hard import failure naming the component and the nodes that use it; see
:func:`reject_empty_component_scripts`.

**Declared packages.** ``additional_packages`` travels in the bundle, on the
orchestration metadata and on each component, and the local runner cannot
install it. The declaration is recorded on the generated ``orchestration.yaml``
under ``additional_packages``, the import warns, and the runner re-checks it at
run time; see :func:`collect_additional_packages`.

**Component config and secret hints.** Component config is bundle-level data,
not node runtime config. Imported projects retain it as ``component_config`` on
each ordinary node, and retain each orchestration's ``secrets_required`` list at
the YAML root. The inverse writer consumes those fields recursively without
rewriting component script or manifest text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..._bundle._node_refs import canonical_node_config
from ..._bundle._orchestration_meta import METADATA_KEY, carried_metadata
from ..._bundle.schema import ComponentExportData, NestedOrchestrationError
from ...validator._component_type import manifest_declared_type
from ._bundle_integrity import (
    collect_additional_packages,
    reject_empty_component_scripts,
)

__all__ = [
    "collect_additional_packages",
    "is_platform_bundle",
    "materialize_platform_bundle",
    "reject_empty_component_scripts",
    "write_platform_project",
]

_FORMAT = "perceivo_platform_bundle_v1"
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")
_COMPONENT_PROJECT_EXCLUDED = {"script", "manifest_yaml", "config", "ports"}
# A synthetic row for an omitted state or policy slot would be a phantom port.
_UNINJECTED_PORT_TYPES = {"state", "policy"}


def is_platform_bundle(data: dict[str, Any]) -> bool:
    """Return whether *data* is a current portable platform bundle."""
    return (
        data.get("export_type") == "orchestration"
        and isinstance(data.get("components"), dict)
        and isinstance(data.get("nodes"), list)
        and isinstance(data.get("ports"), list)
    )


def materialize_platform_bundle(
    data: dict[str, Any],
    directory: str | Path,
    *,
    name_override: str | None = None,
) -> Path:
    """Write a platform bundle as an SDK-runnable project directory."""
    if not is_platform_bundle(data):
        raise ValueError("Input is not a portable platform orchestration bundle")

    meta = data.get("orchestration") or {}
    name = name_override or data.get("new_name") or meta.get("name") or "imported"
    project = Path(directory).resolve() / _project_name(str(name))
    project.mkdir(parents=True, exist_ok=True)
    _write_bundle_project(data, project, display_name=str(name))
    return project


def write_platform_project(data: dict[str, Any], project: Path, *, display_name: str) -> None:
    """Write *data* into the existing directory *project* (caller chose its name)."""
    if not is_platform_bundle(data):
        raise NestedOrchestrationError("Input is not a portable platform orchestration bundle")
    _write_bundle_project(data, project, display_name=display_name)


def _write_bundle_project(data: dict[str, Any], project: Path, *, display_name: str) -> None:
    components = data.get("components") or {}
    nested = data.get("orchestrations") or {}
    # Validate before writing anything, so a corrupt bundle never leaves a
    # half-imported directory. Nested projects are checked by the recursive call.
    reject_empty_component_scripts(data, components, nested)
    referenced = {
        str(node.get("component_ref") or "")
        for node in data.get("nodes", [])
        if isinstance(node, dict)
        and node.get("orchestration_asset_id")
        and node.get("component_ref")
    }
    component_files = _write_components(components, project, skip={*nested, *referenced})
    nested_projects = _write_nested(nested, project)

    nodes = [
        _convert_node(node, component_files, nested_projects, components)
        for node in data.get("nodes", [])
    ]
    ports = _convert_ports(data.get("ports", []), data.get("nodes", []), components)
    edges = _convert_edges(data.get("edges", []), ports)
    _inject_omitted_ports(ports, data.get("nodes", []), components)
    _add_mainflow_edges(nodes, ports, edges)
    meta = data.get("orchestration") or {}
    orchestration = {
        "format": _FORMAT,
        "name": display_name,
        "description": meta.get("description") or "",
        "tags": meta.get("tags"),
        "runtime_config": meta.get("runtime_config"),
        "secrets_required": data.get("secrets_required") or [],
        "nodes": nodes,
        "ports": ports,
        "edges": edges,
    }
    # Record the declared packages so ``perceivo run`` can report missing ones.
    carried = carried_metadata(meta)
    if carried:
        orchestration[METADATA_KEY] = carried
    declared_packages = collect_additional_packages(data)
    if declared_packages:
        orchestration["additional_packages"] = declared_packages
    (project / "orchestration.yaml").write_text(
        yaml.safe_dump(orchestration, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_components(
    components: dict[str, dict[str, Any]], project: Path, *, skip: set[str] | None = None
) -> dict[str, tuple[str, str]]:
    files: dict[str, tuple[str, str]] = {}
    skip = skip or set()
    stems = _unique_project_names(components, "component")
    for ref, component in components.items():
        if ref in skip:
            # The node resolves through ``orchestration_ref`` to the nested project, so
            # this scriptless component definition is redundant.
            continue
        stem = stems[ref]
        script_name = f"{stem}.py"
        manifest_name = f"{stem}.yaml"
        # ``newline=""``: a CRLF script written in text mode on Windows becomes
        # CR CR LF on disk, and re-exports with every line doubled.
        (project / script_name).write_text(
            component.get("script") or "", encoding="utf-8", newline=""
        )
        (project / manifest_name).write_text(
            component.get("manifest_yaml") or "", encoding="utf-8", newline=""
        )
        files[ref] = (script_name, manifest_name)
    return files


def _write_nested(nested: dict[str, dict[str, Any]], project: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    nested_root = project / "nested"
    stems = _unique_project_names(nested, "nested orchestration")
    for ref, child in nested.items():
        child_dir = nested_root / stems[ref]
        child_dir.mkdir(parents=True, exist_ok=True)
        child_meta = child.get("orchestration") or {}
        child_name = child_meta.get("name") or ref
        _write_bundle_project(child, child_dir, display_name=str(child_name))
        paths[ref] = child_dir.relative_to(project).as_posix()
    return paths


def _convert_node(
    node: dict[str, Any],
    component_files: dict[str, tuple[str, str]],
    nested_projects: dict[str, str],
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    node_id = str(node.get("local_id") or node.get("id") or "")
    ref = str(node.get("component_ref") or "")
    converted: dict[str, Any] = {
        "id": node_id,
        "name": node.get("name"),
        "position": node.get("position"),
        "config": canonical_node_config(node.get("config")),
        "component_asset_id": node.get("component_asset_id"),
    }
    orchestration_ref = node.get("orchestration_ref")
    orchestration_asset_id = node.get("orchestration_asset_id")
    if orchestration_ref and orchestration_asset_id:
        raise NestedOrchestrationError(
            f"Node '{node_id}' sets both orchestration_ref ('{orchestration_ref}') "
            f"and orchestration_asset_id ('{orchestration_asset_id}'). Embed a child "
            "OR reference an existing one, not both."
        )
    if orchestration_ref:
        converted["component_type"] = "ORCHESTRATION"
        converted["project"] = nested_projects[str(orchestration_ref)]
        return converted

    if orchestration_asset_id:
        converted["component_type"] = "ORCHESTRATION"
        converted["component_ref"] = ref
        converted["orchestration_asset_id"] = str(orchestration_asset_id)
        return converted

    component = components.get(ref) or {}
    converted["component_type"] = _component_type(component)
    if "config" in component:
        converted["component_config"] = component["config"]
    converted["component_metadata"] = {
        field: component[field]
        for field in ComponentExportData.model_fields
        if field not in _COMPONENT_PROJECT_EXCLUDED and field in component
    }
    if "ports" in component:
        converted["component_ports"] = component["ports"]
    if ref in component_files:
        converted["script"], converted["manifest"] = component_files[ref]
    return converted


def _component_type(component: dict[str, Any]) -> str:
    try:
        manifest = yaml.safe_load(component.get("manifest_yaml") or "") or {}
    except yaml.YAMLError:
        manifest = {}
    # The validator's own manifest reader, so a category-only manifest is not typed AGENT;
    # a bundle whose manifest names no type still carries the platform's category.
    return str(
        manifest_declared_type(manifest)
        or component.get("category")
        or manifest.get("type")
        or component.get("component_type")
        or "AGENT"
    ).upper()


def _convert_ports(
    bundle_ports: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    node_refs = {
        str(node.get("local_id") or node.get("id")): str(node.get("component_ref") or "")
        for node in nodes
    }
    converted: list[dict[str, Any]] = []
    for index, port in enumerate(bundle_ports):
        node_id = str(port.get("node_ref") or "")
        key = str(port.get("component_port_key") or port.get("alias") or f"port_{index}")
        source = _component_port(components.get(node_refs.get(node_id, ""), {}), key)
        item: dict[str, Any] = {
            "id": f"port_{index}",
            "node_id": node_id,
            "name": port.get("alias") or key,
            "alias": port.get("alias"),
            "effective_contract": port.get("effective_contract"),
            "key": key,
            "direction": str(
                port.get("component_port_direction") or source.get("direction") or ""
            ).lower(),
            "port_type": source.get("port_type") or _port_type(port),
        }
        _copy_port_fields(source, item)
        converted.append(item)
    return converted


def _copy_port_fields(source: dict[str, Any], item: dict[str, Any]) -> None:
    for field in (
        "request_data_schema",
        "response_data_schema",
        "artifact_type_input",
        "artifact_type_output",
        "artifact_type_constraint",
        "response_data_passthrough",
        "response_data_passthrough_except",
        "response_data_contract",
    ):
        value = source.get(field)
        if value is not None:
            item[field] = value


def _inject_omitted_ports(
    ports: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> None:
    """Give each node every component port the bundle's port list omitted.

    The platform materializes a bundle this way before it validates or stores
    it: every port of a node's component becomes a node port, whether or not the
    bundle listed it, except state and policy slots. A bundle that omits a
    START's external input still has one on the platform, so the local project
    has one too, and an omitted port nothing connects is reported as unconnected
    exactly as the platform reports it. Appended after the bundle's own ports,
    so edge indices into the bundle's list are unchanged.
    """
    present = {
        (str(port.get("node_id") or ""), str(port.get("key") or ""), port.get("direction"))
        for port in ports
        if not port.get("virtual")
    }
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("local_id") or node.get("id") or "")
        component = components.get(str(node.get("component_ref") or "")) or {}
        for source in component.get("ports") or []:
            if not isinstance(source, dict):
                continue
            if str(source.get("port_type") or "").lower() in _UNINJECTED_PORT_TYPES:
                continue
            key = str(source.get("key") or "")
            direction = str(source.get("direction") or "").lower()
            if not key or (node_id, key, direction) in present:
                continue
            item: dict[str, Any] = {
                "id": f"port_{len(ports)}",
                "node_id": node_id,
                "name": key,
                "alias": None,
                "effective_contract": None,
                "key": key,
                "direction": direction,
                "port_type": source.get("port_type") or direction,
            }
            _copy_port_fields(source, item)
            ports.append(item)
            present.add((node_id, key, direction))


def _component_port(component: dict[str, Any], key: str) -> dict[str, Any]:
    for port in component.get("ports") or []:
        if isinstance(port, dict) and port.get("key") == key:
            return port
    return {}


def _port_type(port: dict[str, Any]) -> str:
    direction = str(port.get("component_port_direction") or "").upper()
    return "in" if direction == "IN" else "out"


def _add_mainflow_edges(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Expand ordinary component mainflow targets into explicit SDK graph edges.

    Nested orchestration targets stay in node config.  Their bundle graph is
    already the authoritative representation, and unlike AGENT/TOOL edges the
    exporter does not translate a synthesized nested edge back into config.
    """
    existing = {(edge["source_port_id"], edge["target_port_id"]) for edge in edges}
    node_ids = {node["id"] for node in nodes}
    for node in nodes:
        if node.get("component_type") == "ORCHESTRATION":
            continue
        config = node.get("config") or {}
        # The next hop may be keyed by node ref, bare node id or node index; normalize
        # all three to a node id. Select by presence: index 0 is a valid target.
        target_id = _resolve_target_node_id(
            _first_set(
                config,
                "mainflow_target_node_ref",
                "mainflow_target_node_id",
                "mainflow_target_node_index",
            ),
            node_ids,
        )
        if not target_id:
            continue
        source = _mainflow_port(ports, node["id"], "out")
        target = _mainflow_port(ports, str(target_id), "in")
        if source is None:
            source = _virtual_port(ports, node["id"], "out")
        if target is None:
            target = _virtual_port(ports, str(target_id), "in")
        pair = (source["id"], target["id"])
        if pair not in existing:
            edges.append(
                {
                    "source_port_id": pair[0],
                    "target_port_id": pair[1],
                    "kind": "MAINFLOW",
                }
            )
            existing.add(pair)


def _first_set(config: dict[str, Any], *keys: str) -> Any:
    """The first key in *keys* that is present and non-empty, else ``None``."""
    for key in keys:
        value = config.get(key)
        if value is not None and value != "":
            return value
    return None


def _resolve_target_node_id(raw: Any, node_ids: set[str]) -> str | None:
    """Normalize a mainflow target reference to a real node id.

    Accepts a full ``node_N`` id or a bare integer ``N`` (mapped to ``node_N``).
    """
    if raw is None or raw == "":
        return None
    text = str(raw)
    if text in node_ids:
        return text
    candidate = f"node_{text}"
    if candidate in node_ids:
        return candidate
    return text


def _mainflow_port(
    ports: list[dict[str, Any]], node_id: str, direction: str
) -> dict[str, Any] | None:
    candidates = [
        port
        for port in ports
        if port.get("node_id") == node_id and port.get("direction") == direction
    ]
    matching = [port for port in candidates if port.get("port_type") == direction]
    return matching[0] if matching else None


def _virtual_port(ports: list[dict[str, Any]], node_id: str, direction: str) -> dict[str, Any]:
    port = {
        "id": f"virtual_{node_id}_{direction}",
        "node_id": node_id,
        "name": "output" if direction == "out" else "input",
        "key": "output" if direction == "out" else "input",
        "direction": direction,
        "port_type": direction,
        "virtual": True,
    }
    ports.append(port)
    return port


def _convert_edges(
    bundle_edges: list[dict[str, Any]], ports: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for edge in bundle_edges:
        source = _edge_index(edge.get("source_port_index"), "source_port_index")
        target = _edge_index(edge.get("target_port_index"), "target_port_index")
        if source >= len(ports) or target >= len(ports):
            raise ValueError("Bundle edge references a port index outside the port list")
        converted.append(
            {
                "source_port_id": ports[source]["id"],
                "target_port_id": ports[target]["id"],
                "kind": edge.get("edge_kind") or "MAINFLOW",
            }
        )
    return converted


def _edge_index(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Bundle edge {field} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"Bundle edge {field} must be a non-negative integer")
    return value


def _unique_project_names(values: dict[str, Any], kind: str) -> dict[str, str]:
    """Return sanitized names, rejecting refs that would overwrite each other."""
    names: dict[str, str] = {}
    owners: dict[str, str] = {}
    for ref in values:
        name = _project_name(str(ref))
        previous = owners.get(name)
        if previous is not None and previous != ref:
            raise ValueError(f"Bundle {kind} refs {previous!r} and {ref!r} both map to {name!r}")
        owners[name] = ref
        names[ref] = name
    return names


def _project_name(value: str) -> str:
    cleaned = _SAFE_NAME.sub("-", value.strip()).strip("-_")
    return cleaned or "imported"
