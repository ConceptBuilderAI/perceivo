"""Compile an orchestration graph into NodeShells.

The local runner and the platform engine compile the same graph the same way, so
port binding, mainflow ordering and edge resolution cannot diverge between them.
:func:`compile_orchestration` takes the graph itself; :func:`compile_project`
reads one from a project directory.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import yaml

from ..types.errors import ConfigurationError
from .loader import load_manifest
from .types import CompileResult, NodeShell, PortConnection

__all__ = ["compile_orchestration", "compile_project"]

logger = logging.getLogger("perceivo.runner.compiler")


def compile_orchestration(
    graph: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> CompileResult:
    """Compile an orchestration graph into an execution plan.

    Args:
        graph: The orchestration structure: ``nodes``, ``ports`` and ``edges``.
            Exactly what ``orchestration.yaml`` parses to, and equally what an
            engine that never touched a file already holds.
        base_dir: Directory the per-node ``script`` / ``manifest`` / ``project``
            names resolve against. The default, ``None``, resolves **no
            relative name**, so a graph-only compile returns the same plan from
            every working directory instead of silently binding whatever happens
            to sit in the caller's cwd. Absolute names still resolve.

    Returns:
        A :class:`CompileResult` with the compiled execution plan.

    Raises:
        TypeError: If handed a path instead of a graph.
        RuntimeError: If the orchestration is malformed.
        ConfigurationError: If a node config names a mainflow target that is
            not a declared node.
    """
    if isinstance(graph, (str, Path)):
        raise TypeError(
            f"compile_orchestration() takes an orchestration graph mapping, not "
            f"{type(graph).__name__}. Use compile_project({graph!r}) to compile a "
            f"project directory from disk."
        )

    base = Path(base_dir).resolve() if base_dir is not None else None

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    ports = graph.get("ports", [])

    if not nodes:
        raise RuntimeError("Orchestration has no nodes defined.")

    shells = _build_shells(base, nodes)

    port_to_node, port_name_map = _build_port_maps(ports)
    _process_edges(edges, shells, port_to_node, port_name_map)
    _apply_config_targets(shells)

    start_id, end_id = _find_start_end(shells)
    if not start_id:
        raise RuntimeError("No START node found in orchestration.")
    if not end_id:
        raise RuntimeError("No END node found in orchestration.")

    _wire_terminals(shells, end_id)
    root_id = _resolve_root(shells, start_id)

    logger.info(
        "Compiled orchestration: %d nodes, start=%s, root=%s, end=%s",
        len(shells),
        start_id,
        root_id,
        end_id,
    )

    return CompileResult(
        root_id=root_id,
        start_node_id=start_id,
        end_node_id=end_id,
        shells_by_id=shells,
        additional_packages=_declared_packages(graph),
    )


def _declared_packages(graph: Mapping[str, Any]) -> list[str]:
    """Read the orchestration's ``additional_packages`` declaration.

    ``perceivo import-bundle`` records the union of every
    ``additional_packages`` in the bundle here, so the runner can name what this
    interpreter is missing. Non-list values are ignored rather than raising: a
    malformed dependency hint must not stop an orchestration from compiling.
    """
    raw = graph.get("additional_packages")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def compile_project(project_dir: str | Path) -> CompileResult:
    """Compile a project directory by reading its ``orchestration.yaml``.

    Loads the file and passes the parsed graph to :func:`compile_orchestration`
    with the project directory as ``base_dir``, so ``script`` / ``manifest`` /
    ``project`` names resolve relative to the project.

    Args:
        project_dir: Path to the orchestration project directory.

    Returns:
        A :class:`CompileResult` with the compiled execution plan.

    Raises:
        FileNotFoundError: If ``orchestration.yaml`` doesn't exist.
        RuntimeError: If the orchestration is malformed.
    """
    path = Path(project_dir).resolve()
    return compile_orchestration(_load_orchestration_file(path), base_dir=path)


def _load_orchestration_file(path: Path) -> dict[str, Any]:
    """Load and parse ``orchestration.yaml`` from the project directory."""
    orch_file = path / "orchestration.yaml"
    if not orch_file.exists():
        raise FileNotFoundError(
            f"orchestration.yaml not found in {path}. "
            f"Create one or run 'perceivo new orchestration'."
        )

    with open(orch_file, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_shells(base: Optional[Path], nodes: list[Any]) -> dict[str, NodeShell]:
    """Build the :class:`NodeShell` map from orchestration node definitions."""
    shells: dict[str, NodeShell] = {}
    for node in nodes:
        node_id = node["id"]
        ctype = node.get("component_type", "").upper()

        shell = NodeShell(
            node_id=node_id,
            name=node.get("name", node_id),
            component_type=ctype,
            script_path=_resolve_file(base, node.get("script")),
            manifest_path=_resolve_file(base, node.get("manifest")),
            project_path=_resolve_directory(base, node.get("project")),
            config=node.get("config") or {},
        )

        if shell.manifest_path:
            manifest = load_manifest(shell.manifest_path)
            shell.input_parameters = manifest.get("input_parameters", {})
            shell.secret_bindings = manifest.get("secret_bindings", {})
            shell.fsm_states = manifest.get("fsm_states", [])
            _classify_manifest_ports(shell, manifest)

        shells[node_id] = shell

    return shells


def _classify_manifest_ports(shell: NodeShell, manifest: Mapping[str, Any]) -> None:
    """Sort the manifest's sectioned port declarations onto the shell by type."""
    from ..validator._port_rules import _manifest_ports

    for port in _manifest_ports(dict(manifest)):
        entry = {"name": port["key"], "port_type": port["port_type"]}
        ptype = port["port_type"].lower()
        if ptype in ("in", "external_in"):
            shell.in_ports.append(entry)
        elif ptype in ("out", "external_out"):
            shell.out_ports.append(entry)
        elif ptype == "state":
            shell.state_ports.append(entry)
        elif ptype == "policy":
            shell.policy_ports.append(entry)


def _build_port_maps(ports: list[Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Build ``port_id -> node_id`` and ``port_id -> port name`` mappings."""
    port_to_node: dict[str, str] = {}
    port_name_map: dict[str, str] = {}  # port_id -> port name/key
    for port in ports:
        port_id = port.get("id", "")
        node_id = port.get("node_id", "")
        port_to_node[port_id] = node_id
        port_name_map[port_id] = port.get("name", port.get("key", port_id))
    return port_to_node, port_name_map


def _process_edges(
    edges: list[Any],
    shells: dict[str, NodeShell],
    port_to_node: dict[str, str],
    port_name_map: dict[str, str],
) -> None:
    """Apply every edge to the shell graph."""
    for edge in edges:
        _apply_edge(edge, shells, port_to_node, port_name_map)


def _apply_edge(
    edge: dict[str, Any],
    shells: dict[str, NodeShell],
    port_to_node: dict[str, str],
    port_name_map: dict[str, str],
) -> None:
    """Wire a single edge as a mainflow successor or an invoke connection.

    Every edge must resolve. An edge that cannot be wired means the compiled
    graph is not the graph the author declared, so it raises rather than being
    skipped: a silently dropped edge produces a runner that executes a smaller
    orchestration and still reports success, and any conformance suite built on
    that passes while both executors drop the same edge.
    """
    source_port_id = edge.get("source_port_id", "")
    target_port_id = edge.get("target_port_id", "")
    edge_kind = edge.get("kind", edge.get("edge_kind", "MAINFLOW"))

    source_node_id = _resolve_edge_node(source_port_id, "source", edge_kind, shells, port_to_node)
    target_node_id = _resolve_edge_node(target_port_id, "target", edge_kind, shells, port_to_node)

    source_shell = shells[source_node_id]
    target_shell = shells[target_node_id]

    if edge_kind == "MAINFLOW":
        if target_node_id not in source_shell.mainflow_successors:
            source_shell.mainflow_successors.append(target_node_id)
    elif edge_kind == "INVOKE":
        port_name = port_name_map.get(source_port_id, source_port_id)
        conn = PortConnection(
            to_node_id=target_node_id,
            to_port_id=target_port_id,
            to_component_type=target_shell.component_type,
        )
        source_shell.invoke_connections.setdefault(port_name, []).append(conn)


def _resolve_edge_node(
    port_id: str,
    role: str,
    edge_kind: str,
    shells: dict[str, NodeShell],
    port_to_node: dict[str, str],
) -> str:
    """Resolve one endpoint of an edge to a node id, or raise saying why.

    The three failure modes are reported separately because they have different
    causes: an unknown port id is a dangling reference, a port carrying no
    ``node_id`` is a malformed port record, and a node id absent from the shell
    map means the edge points at a node the orchestration never declared.
    """
    where = f"{edge_kind} edge {role} port {port_id!r}"

    if port_id not in port_to_node:
        raise RuntimeError(
            f"{where} does not match any declared port. "
            f"Every edge endpoint must reference a port in the orchestration's port list."
        )

    node_id = port_to_node[port_id]
    if not node_id:
        raise RuntimeError(f"{where} resolves to a port with no node_id.")
    if node_id not in shells:
        raise RuntimeError(f"{where} resolves to node {node_id!r}, which is not a declared node.")

    return node_id


_CONFIG_TARGET_KEYS = (
    "mainflow_target_node_ref",
    "mainflow_target_node_id",
    "mainflow_target_node_index",
)


def _apply_config_targets(shells: dict[str, NodeShell]) -> None:
    """Honor a node's config-declared mainflow target, as the engine does.

    The platform's mainflow is the union of explicit MAINFLOW edges and each node's
    config ``mainflow_target_node_id``. ``perceivo import-bundle`` turns that config
    into a real edge for every node type except ORCHESTRATION, whose hop is applied
    here.

    Edge successors keep precedence; the config target is added when absent.

    Raises:
        ConfigurationError: The target names no declared node.
    """
    for shell in shells.values():
        config = shell.config if isinstance(shell.config, dict) else {}
        raw = next(
            (
                config[key]
                for key in _CONFIG_TARGET_KEYS
                if config.get(key) is not None and config.get(key) != ""
            ),
            None,
        )
        if raw is None:
            continue
        text = str(raw)
        target = text if text in shells else f"node_{text}"
        if target not in shells:
            raise ConfigurationError(
                f"Node {shell.node_id!r} declares mainflow target {text!r} in its "
                f"config, which is not a declared node."
            )
        if target not in shell.mainflow_successors:
            shell.mainflow_successors.append(target)


def _find_start_end(shells: dict[str, NodeShell]) -> tuple[str, str]:
    """Locate the START and END node ids, rejecting duplicates of either.

    Returns empty strings when a terminal is absent; the caller raises the
    "no START/END node found" errors.
    """
    start_ids = [sid for sid, shell in shells.items() if shell.component_type == "START"]
    end_ids = [sid for sid, shell in shells.items() if shell.component_type == "END"]

    for kind, found in (("START", start_ids), ("END", end_ids)):
        if len(found) > 1:
            raise RuntimeError(
                f"Orchestration declares {len(found)} {kind} nodes ({', '.join(found)}); "
                f"exactly one is required."
            )

    return (start_ids[0] if start_ids else "", end_ids[0] if end_ids else "")


def _wire_terminals(shells: dict[str, NodeShell], end_id: str) -> None:
    """Connect dangling non-terminal nodes to END."""
    for sid, shell in shells.items():
        if sid == end_id:
            continue
        if not shell.mainflow_successors and shell.component_type not in ("POLICY", "REVIEW"):
            shell.mainflow_successors.append(end_id)


def _resolve_root(shells: dict[str, NodeShell], start_id: str) -> str:
    """Return the root node, the first mainflow successor of START."""
    start_shell = shells[start_id]
    if start_shell.mainflow_successors:
        return start_shell.mainflow_successors[0]
    raise RuntimeError("START node has no mainflow successors.")


def _resolve_directory(base: Optional[Path], dirname: Optional[str]) -> Optional[str]:
    """Resolve a nested orchestration project relative to its parent."""
    resolved = _against_base(base, dirname)
    return str(resolved) if resolved is not None and resolved.is_dir() else None


def _resolve_file(base: Optional[Path], filename: Optional[str]) -> Optional[str]:
    """Resolve a filename relative to the project directory."""
    resolved = _against_base(base, filename)
    return str(resolved) if resolved is not None and resolved.exists() else None


def _against_base(base: Optional[Path], name: Optional[str]) -> Optional[Path]:
    """Join *name* onto *base*, or ``None`` when it cannot be located.

    With no base, a relative name is not resolved, so a graph compiled from memory
    yields the same script paths from every working directory.
    """
    if not name:
        return None
    candidate = Path(name)
    if base is not None:
        return base / name
    return candidate if candidate.is_absolute() else None
