"""Convert a local orchestration project ⇄ the platform ``OrchestrationExport`` bundle.

``orchestration_to_bundle`` is the inverse of
``perceivo.orchestration._builder._bundle_project.materialize_platform_bundle``
(the bundle→files reader that ``bundle_to_orchestration`` reuses): assign
``comp_N`` component refs, reference ports by ``(node_ref, key, direction)``,
and index edges by port position.

A node may declare ports its component manifest does not; ``add_node(extra_ports=
...)`` writes exactly that. The platform importer resolves every orchestration
port row against the component's ports, so a node-declared port is emitted onto
the component definition (``component.node_declared_ports``). The port set is
therefore part of the component's identity, and the ``comp_N`` dedup key is
``(script, manifest, port signature, component config)``. A node port named
differently from the manifest's is a second port, so a START or END whose node
renames its external port is refused with the platform's own message.

A ``MAINFLOW`` edge whose source is an AGENT or TOOL node's ``out`` port is
rejected by the platform (``VAL-MAINFLOW-OUT-001``). Such an edge is translated
at export time: the next hop is written to the source node's
``config.mainflow_target_node_ref``, the edge is dropped, and the orphaned
out-port is removed from the ``ports`` list and from the component
(``._mainflow_ports``). The reader re-adds the mainflow edge from that config,
so a files→bundle→files round trip is functionally stable.

Nested-orchestration nodes (``component_type ORCHESTRATION``) keep their place in
``nodes`` and carry an ``orchestration_ref`` into the sibling ``orchestrations``
map, which holds the child's own full bundle. The child is built from the
directory the node's ``project`` field names; a nested node that cannot be
resolved raises.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from perceivo._bundle._component_references import component_base
from perceivo._bundle._mainflow_ports import retire_translated_out_ports
from perceivo._bundle._node_refs import canonical_node_config
from perceivo._bundle._orchestration_meta import export_metadata
from perceivo._bundle._port_consistency import (
    absorb_synthesized_target,
    reject_terminal_port_shape,
    reject_undeclared_ports,
    set_mainflow_target,
)
from perceivo._bundle.component import (
    _COMPONENT_PROJECT_EXCLUDED,
    BundleConfigError,
    _port_direction,
    bundled_component_ports,
    read_text,
    restore_component_export_data,
)
from perceivo._bundle.schema import (
    ComponentExportData,
    EdgeExportData,
    NodeExportData,
    OrchestrationExport,
    PortExportData,
    PortRefExportData,
)
from perceivo._bundle.schema import (
    NestedOrchestrationError as NestedOrchestrationError,
)
from perceivo._internal.paths import resolve_within_project

# Node types whose ``out`` mainflow edge the server rejects and that we instead
# encode via ``config.mainflow_target_node_ref``.
_MAINFLOW_CONFIG_TYPES = frozenset({"AGENT", "TOOL"})


def _direction_upper(port: dict[str, Any]) -> str:
    """IN/OUT direction for an orchestration.yaml port (shares the port_type map)."""
    return _port_direction(port)


def _port_signature(ports: list[PortExportData]) -> tuple[str, ...]:
    """Canonical identity of a set of node-declared ports, for component dedup.

    Two nodes sharing one ``(script, manifest)`` pair but declaring different
    ports must not collapse onto one component definition; each would inherit
    the other's ports as canvas-visible phantoms. The whole port payload is
    hashed, not just the key, so nodes that declare the same port names with
    different contracts still separate. ``node_declared_ports`` already returns
    a canonically ordered list, so declaration order does not split a component.
    """
    return tuple(json.dumps(p.model_dump(mode="json"), sort_keys=True) for p in ports)


def _component_config(node: dict[str, Any], base: ComponentExportData) -> dict[str, Any] | None:
    """The component config retained on an imported node, or its manifest value.

    ``component_config`` is deliberately separate from the node's runtime
    ``config``. Presence is significant: an explicit null from a platform
    bundle overrides declarations that the preserved manifest could derive.
    """
    if "component_config" not in node:
        return base.config
    config = node["component_config"]
    if config is not None and not isinstance(config, dict):
        raise BundleConfigError(
            f"Node {node.get('id')!r} component_config must be a mapping or null"
        )
    return config


def _component_config_signature(config: dict[str, Any] | None) -> str:
    """Canonical component-config identity for correct component deduplication."""
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def orchestration_to_bundle(project_dir: str | Path) -> dict[str, Any]:
    """Build a platform ``OrchestrationExport`` bundle from a local project.

    Args:
        project_dir: a project directory containing ``orchestration.yaml`` and the
            referenced component ``.py`` / ``.yaml`` files. Nested-orchestration
            nodes are followed into the directory their ``project`` field names.

    Returns:
        The bundle as a JSON-ready dict (``export_type: "orchestration"``).

    Raises:
        FileNotFoundError: no ``orchestration.yaml`` here or in a nested project.
        NestedOrchestrationError: an ORCHESTRATION node declares no ``project``,
            or the nesting is cyclic. Also a ``ValueError``.
    """
    export = _build_export(Path(project_dir).resolve(), ())
    return _prune_absent_nesting(export.model_dump(mode="json"))


def _prune_absent_nesting(data: dict[str, Any]) -> dict[str, Any]:
    """Drop absent nesting keys wherever they are ``None``.

    ``model_dump`` emits every field, so a flat orchestration would otherwise carry
    ``"orchestrations": null`` and an ``"orchestration_ref": null`` per node, keys
    the platform does not write.
    """
    if data.get("orchestrations") is None:
        data.pop("orchestrations", None)
    else:
        for child in data["orchestrations"].values():
            _prune_absent_nesting(child)
    for node in data.get("nodes") or []:
        if node.get("orchestration_ref") is None:
            node.pop("orchestration_ref", None)
        if node.get("orchestration_asset_id") is None:
            node.pop("orchestration_asset_id", None)
    return data


def _child_project(project: Path, node: dict[str, Any], node_id: str) -> Path:
    """Resolve an ORCHESTRATION node's child project directory, or raise.

    Raises:
        NestedOrchestrationError: The node declares no ``project``.
        FileNotFoundError: The named project has no ``orchestration.yaml``.
    """
    raw = node.get("project")
    if not raw:
        raise NestedOrchestrationError(
            f"Node {node_id!r} is component_type ORCHESTRATION but declares no "
            f"'project' directory, so its child orchestration cannot be exported."
        )
    # Graph-supplied, so confined to the project: an escaped child would have
    # every script it names read as well.
    child = resolve_within_project(project, raw, field="project", node_id=node_id)
    if not (child / "orchestration.yaml").exists():
        raise FileNotFoundError(
            f"Node {node_id!r} names nested project {raw!r}, but there is no "
            f"orchestration.yaml at {child}."
        )
    return child


def _load_orchestration_yaml(project: Path, ancestors: tuple[Path, ...]) -> dict[str, Any]:
    """Refuse a nesting cycle, then read *project*'s ``orchestration.yaml``."""
    if project in ancestors:
        chain = " -> ".join(p.name for p in (*ancestors, project))
        raise NestedOrchestrationError(f"Nested orchestrations form a cycle: {chain}")

    orch_file = project / "orchestration.yaml"
    if not orch_file.exists():
        raise FileNotFoundError(f"No orchestration.yaml in {project}")
    return yaml.safe_load(read_text(orch_file)) or {}


def _port_info_by_id(raw_ports: list[dict[str, Any]]) -> dict[str, tuple[str, str, str, str]]:
    """Port lookup: sdk port id -> (node_id, key, direction, port_type)."""
    port_info: dict[str, tuple[str, str, str, str]] = {}
    for port in raw_ports:
        port_info[str(port.get("id"))] = (
            str(port.get("node_id") or ""),
            str(port.get("key") or port.get("name") or ""),
            _direction_upper(port),
            str(port.get("port_type") or ""),
        )
    return port_info


def _translate_mainflow_edges(
    raw_edges: list[dict[str, Any]],
    port_info: dict[str, tuple[str, str, str, str]],
    node_type: dict[str, str],
    node_config: dict[str, dict[str, Any] | None],
    raw_ports: list[dict[str, Any]],
) -> tuple[list[tuple[str, str, str]], set[str]]:
    """Translate an AGENT/TOOL ``out`` MAINFLOW edge into ``config.mainflow_target_node_ref``.

    The platform rejects such an edge (``VAL-MAINFLOW-OUT-001``). The next hop is
    written to the source node's config (mutating *node_config*), the edge is
    dropped, and the orphaned out-port is dropped. This runs before the components
    dict is built, so a dropped out-port is never carried onto the component
    definition.

    A MAINFLOW edge leaving a reader-synthesized port takes the same route whatever
    the node's type, and every synthesized port is dropped even when no edge
    references it.

    Returns:
        The kept ``(source_id, target_id, kind)`` edges and the dropped port ids.
    """
    kept_edges: list[tuple[str, str, str]] = []
    synthesized = {str(p.get("id")) for p in raw_ports if p.get("virtual")}
    dropped_out_ports: set[str] = set(synthesized)
    for edge in raw_edges:
        kind = str(edge.get("kind") or "MAINFLOW")
        s_id = str(edge.get("source_port_id"))
        t_id = str(edge.get("target_port_id"))
        s_info = port_info.get(s_id)
        t_info = port_info.get(t_id)
        if s_info is None or t_info is None:
            continue  # references an unknown/virtual port
        s_node, _, _, s_ptype = s_info
        if kind == "MAINFLOW" and (
            s_id in synthesized
            or (node_type.get(s_node) in _MAINFLOW_CONFIG_TYPES and s_ptype == "out")
        ):
            set_mainflow_target(node_config, s_node, t_info[0])
            dropped_out_ports.add(s_id)
            continue
        if absorb_synthesized_target(node_config, s_node, t_id, t_info[0], kind, synthesized):
            continue
        kept_edges.append((s_id, t_id, kind))
    return kept_edges, dropped_out_ports


def _ports_by_node(
    raw_ports: list[dict[str, Any]], dropped_out_ports: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Node-level port declarations the bundle will emit a port ref for, by node.

    These are the ports the component definition has to absorb.
    """
    ports_by_node: dict[str, list[dict[str, Any]]] = {}
    for port in raw_ports:
        if str(port.get("id")) in dropped_out_ports:
            continue
        ports_by_node.setdefault(str(port.get("node_id") or ""), []).append(port)
    return ports_by_node


def _component_refs(
    project: Path,
    ancestors: tuple[Path, ...],
    raw_nodes: list[dict[str, Any]],
    node_type: dict[str, str],
    ports_by_node: dict[str, list[dict[str, Any]]],
    manifest_ports: dict[str, list[PortExportData]],
) -> tuple[
    dict[str, ComponentExportData],
    dict[str, OrchestrationExport],
    dict[str, str],
    dict[str, str],
]:
    """Components dict (distinct script/manifest/port-set -> comp_N).

    ORCHESTRATION nodes take a parallel ref space (orch_N) into the sibling
    ``orchestrations`` map instead: the platform writes the *same* ref into
    both ``component_ref`` and ``orchestration_ref`` and puts nothing in
    ``components`` for it.

    Records in *manifest_ports* the manifest's own ports of each component a
    node merges its ports onto.

    Returns ``(components, nested, nested_ref_by_node, ref_by_node)``.
    """
    components: dict[str, ComponentExportData] = {}
    nested: dict[str, OrchestrationExport] = {}
    nested_ref_by_node: dict[str, str] = {}
    parsed_by_files: dict[tuple[str, str], ComponentExportData] = {}
    ref_by_identity: dict[tuple[str, str, tuple[str, ...], str, str], str] = {}
    ref_by_node: dict[str, str] = {}
    for node in raw_nodes:
        nid = str(node.get("id") or "")
        if node_type.get(nid) == "ORCHESTRATION":
            ref_by_node[nid] = _orchestration_node_ref(
                project, ancestors, node, nid, nested, nested_ref_by_node
            )
            continue
        script = node.get("script")
        manifest = node.get("manifest")
        ref = ""
        if script:
            files_key = (str(script), str(manifest or ""))
            base = component_base(project, nid, script, manifest, files_key, parsed_by_files)
            base = restore_component_export_data(base, node)
            merged_ports = bundled_component_ports(base, node, ports_by_node.get(nid, []))
            component_config = _component_config(node, base)
            identity = (
                *files_key,
                _port_signature(merged_ports),
                _component_config_signature(component_config),
                json.dumps(
                    base.model_dump(mode="json", exclude=_COMPONENT_PROJECT_EXCLUDED),
                    sort_keys=True,
                ),
            )
            ref = ref_by_identity.get(identity, "")
            if not ref:
                ref = f"comp_{len(ref_by_identity)}"
                ref_by_identity[identity] = ref
                components[ref] = base.model_copy(
                    update={"ports": merged_ports, "config": component_config}
                )
            if "component_ports" not in node:
                manifest_ports[ref] = base.ports
        ref_by_node[nid] = ref
    return components, nested, nested_ref_by_node, ref_by_node


def _orchestration_node_ref(
    project: Path,
    ancestors: tuple[Path, ...],
    node: dict[str, Any],
    nid: str,
    nested: dict[str, OrchestrationExport],
    nested_ref_by_node: dict[str, str],
) -> str:
    """Return an imported ref or build and register a nested local project."""
    if node.get("orchestration_asset_id"):
        return str(node.get("component_ref") or "")

    nested_ref = f"orch_{len(nested)}"
    nested[nested_ref] = _build_export(_child_project(project, node, nid), (*ancestors, project))
    nested_ref_by_node[nid] = nested_ref
    return nested_ref


def _export_ports(
    raw_ports: list[dict[str, Any]], dropped_out_ports: set[str]
) -> tuple[list[PortRefExportData], dict[str, int]]:
    """Ports (with an index map for edges), skipping converted-mainflow out ports."""
    port_index: dict[str, int] = {}
    ports: list[PortRefExportData] = []
    for port in raw_ports:
        pid = str(port.get("id"))
        if pid in dropped_out_ports:
            continue
        port_index[pid] = len(ports)
        ports.append(
            PortRefExportData(
                node_ref=str(port.get("node_id") or ""),
                component_port_key=str(port.get("key") or port.get("name") or ""),
                component_port_direction=_direction_upper(port),
                alias=port["alias"] if "alias" in port else port.get("name"),
                effective_contract=port.get("effective_contract"),
            )
        )
    return ports, port_index


def _export_edges(
    kept_edges: list[tuple[str, str, str]], port_index: dict[str, int]
) -> list[EdgeExportData]:
    """Edges by port index, skipping any that reference an unknown/dropped port."""
    edges: list[EdgeExportData] = []
    for s_id, t_id, kind in kept_edges:
        s = port_index.get(s_id)
        t = port_index.get(t_id)
        if s is None or t is None:
            continue
        edges.append(EdgeExportData(source_port_index=s, target_port_index=t, edge_kind=kind))
    return edges


def _export_nodes(
    raw_nodes: list[dict[str, Any]],
    ref_by_node: dict[str, str],
    nested_ref_by_node: dict[str, str],
    node_config: dict[str, dict[str, Any] | None],
) -> list[NodeExportData]:
    """Nodes; each config carries any translated mainflow target."""
    nodes: list[NodeExportData] = []
    for node in raw_nodes:
        nid = str(node.get("id") or "")
        nodes.append(
            NodeExportData(
                local_id=nid,
                component_ref=ref_by_node.get(nid, ""),
                orchestration_ref=nested_ref_by_node.get(nid),
                component_asset_id=node.get("component_asset_id"),
                orchestration_asset_id=node.get("orchestration_asset_id"),
                name=node.get("name"),
                position=node.get("position"),
                config=node_config.get(nid),
            )
        )
    return nodes


def _build_export(project: Path, ancestors: tuple[Path, ...]) -> OrchestrationExport:
    """Build one orchestration's export model, recursing into nested projects.

    *ancestors* is the chain of enclosing project directories. A project that
    reappears in its own chain is a cycle: without this check the recursion runs
    until the interpreter's stack limit and reports a ``RecursionError`` naming
    no orchestration.
    """
    data = _load_orchestration_yaml(project, ancestors)

    raw_nodes = data.get("nodes") or []
    raw_ports = data.get("ports") or []
    raw_edges = data.get("edges") or []

    node_type = {
        str(node.get("id") or ""): str(node.get("component_type") or "").upper()
        for node in raw_nodes
    }
    # Per-node config copies so mainflow translation never mutates the source
    # YAML dicts.
    node_config: dict[str, dict[str, Any] | None] = {}
    for node in raw_nodes:
        config = node.get("config")
        if config is not None and not isinstance(config, dict):
            raise BundleConfigError(f"Node {node.get('id')!r} config must be mapping or null")
        node_config[str(node.get("id") or "")] = canonical_node_config(
            dict(config) if config is not None else None
        )

    port_info = _port_info_by_id(raw_ports)
    kept_edges, dropped_out_ports = _translate_mainflow_edges(
        raw_edges, port_info, node_type, node_config, raw_ports
    )
    ports_by_node = _ports_by_node(raw_ports, dropped_out_ports)
    manifest_ports: dict[str, list[PortExportData]] = {}
    components, nested, nested_ref_by_node, ref_by_node = _component_refs(
        project, ancestors, raw_nodes, node_type, ports_by_node, manifest_ports
    )
    ports, port_index = _export_ports(raw_ports, dropped_out_ports)
    retire_translated_out_ports(components, ref_by_node, raw_ports, dropped_out_ports, ports)
    reject_undeclared_ports(ports, ref_by_node, components, set(nested_ref_by_node))
    pinned_refs = {
        ref_by_node.get(str(n.get("id") or ""), "") for n in raw_nodes if "component_ports" in n
    }
    reject_terminal_port_shape(components, pinned_refs, manifest_ports)
    edges = _export_edges(kept_edges, port_index)
    nodes = _export_nodes(raw_nodes, ref_by_node, nested_ref_by_node, node_config)

    return OrchestrationExport(
        exported_at=datetime.now(UTC),
        orchestration=export_metadata(data, project),
        components=components,
        nodes=nodes,
        ports=ports,
        edges=edges,
        secrets_required=data.get("secrets_required") or [],
        orchestrations=nested or None,
    )


def bundle_to_orchestration(
    bundle: dict[str, Any],
    directory: str | Path = ".",
    *,
    name_override: str | None = None,
) -> Path:
    """Write a platform ``OrchestrationExport`` bundle out as a local project.

    Thin wrapper over the existing bundle→files reader. Imported lazily to avoid a
    package import cycle (``builder`` ← ``_bundle`` ← ``builder._bundle_project``).
    """
    from perceivo.orchestration._builder._bundle_project import materialize_platform_bundle

    return materialize_platform_bundle(bundle, directory, name_override=name_override)
