"""Structural checks: mainflow, END connections, FSM depth."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .._report import ValidationCheck
from ._constants import MAINFLOW_COMPONENT_TYPES, MAX_FSM_DEPTH


def _validate_mainflow_structure(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    node_type: Any,
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    start_nodes = [n for n in nodes if node_type(n["id"]) == "START"]
    end_nodes = [n for n in nodes if node_type(n["id"]) == "END"]

    if len(start_nodes) != 1:
        checks.append(
            ValidationCheck(
                id="ORCH-FLOW-001",
                severity="error",
                category="graph",
                title="START node count",
                message=f"Orchestration must have exactly 1 START node, found {len(start_nodes)}.",
            )
        )
    if len(end_nodes) != 1:
        checks.append(
            ValidationCheck(
                id="ORCH-FLOW-002",
                severity="error",
                category="graph",
                title="END node count",
                message=f"Orchestration must have exactly 1 END node, found {len(end_nodes)}.",
            )
        )

    mainflow_edges = [e for e in edges if e.get("kind", e.get("edge_kind", "")) == "MAINFLOW"]
    port_map = {p["id"]: p for p in ports}
    for edge in mainflow_edges:
        target_port = port_map.get(edge.get("target_port_id", ""))
        if target_port:
            target_type = node_type(target_port.get("node_id", ""))
            if target_type and target_type not in MAINFLOW_COMPONENT_TYPES | {"START", "END"}:
                checks.append(
                    ValidationCheck(
                        id=f"ORCH-FLOW-{len(checks) + 3:03d}",
                        severity="error",
                        category="graph",
                        title="Invalid mainflow component",
                        message=f"{target_type} is not valid on the mainflow. "
                        f"Valid types: {', '.join(sorted(MAINFLOW_COMPONENT_TYPES))}",
                    )
                )

    # Appended after the loop on purpose: the ids above are positional
    # (``len(checks) + 3``), so extending before it would renumber them.
    checks.extend(_mainflow_leaving_end(mainflow_edges, port_map, node_type))
    return checks


def _mainflow_leaving_end(
    mainflow_edges: list[dict[str, Any]],
    port_map: dict[str, dict[str, Any]],
    node_type: Any,
) -> list[ValidationCheck]:
    """Report any edge that originates from the terminal END node.

    The connection matrix is skipped for MAINFLOW edges because the platform keeps
    the mainflow in node config. The platform still rejects a MAINFLOW edge out of
    END on import, so the terminus is checked here as a terminal-node rule.
    """
    checks: list[ValidationCheck] = []
    for edge in mainflow_edges:
        source_port = port_map.get(edge.get("source_port_id", ""))
        if not source_port:
            continue
        if node_type(source_port.get("node_id", "")) == "END":
            checks.append(
                ValidationCheck(
                    id="ORCH-FLOW-END-001",
                    severity="error",
                    category="graph",
                    title="Edge leaves the END node",
                    message="END is terminal: no edge may originate from it. "
                    f"Source port '{source_port.get('key') or source_port.get('id')}' "
                    f"is on END node '{source_port.get('node_id')}'.",
                )
            )
    return checks


def _validate_end_connections(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    port_map: dict[str, dict[str, Any]],
    node_type: Any,
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    end_nodes = {n["id"] for n in nodes if node_type(n["id"]) == "END"}

    for edge in edges:
        target_port = port_map.get(edge.get("target_port_id", ""))
        if not target_port:
            continue
        if target_port.get("node_id") not in end_nodes:
            continue

        source_port = port_map.get(edge.get("source_port_id", ""))
        if not source_port:
            continue

        source_type = node_type(source_port.get("node_id", ""))
        if source_type and source_type not in MAINFLOW_COMPONENT_TYPES:
            checks.append(
                ValidationCheck(
                    id=f"ORCH-END-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="Invalid END connection",
                    message=f"END node receives from {source_type}, which is not a mainflow component.",
                )
            )

        # A bidirectional state port expects a response END will never send; the direction
        # rule misses it because a state port is a legal edge source.
        if (source_port.get("port_type") or "").strip().lower() == "state":
            key = source_port.get("key") or source_port.get("name") or source_port.get("id")
            checks.append(
                ValidationCheck(
                    id=f"ORCH-END-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="Invalid END connection",
                    message=f"END component input must be connected to a unidirectional "
                    f"mainflow OUT port, not a bidirectional state port. Source port "
                    f"'{key}' on node '{source_port.get('node_id')}' is a state port.",
                )
            )

    return checks


def _invoke_target_node(
    edge: dict[str, Any],
    port_map: dict[str, dict[str, Any]],
    start_id: Any,
) -> Any:
    """Return the target node id of an INVOKE edge leaving ``start_id``, else None."""
    source_port = port_map.get(edge.get("source_port_id", ""))
    target_port = port_map.get(edge.get("target_port_id", ""))
    if not source_port or not target_port:
        return None
    if source_port.get("node_id") != start_id:
        return None
    return target_port.get("node_id", "")


_WHITE, _GRAY, _BLACK = 0, 1, 2


def _compute_fsm_depths(
    adjacency: dict[str, set[str]],
) -> tuple[dict[str, int], set[str]]:
    """Compute memoized FSM depths and detect cycles in ``O(V + E)``."""
    color: dict[str, int] = dict.fromkeys(adjacency, _WHITE)
    depth: dict[str, int] = {}
    best: dict[str, int] = {}
    cycle_nodes: set[str] = set()

    for start in adjacency:
        if color[start] != _WHITE:
            continue
        _fsm_depth_dfs(start, adjacency, color, depth, best, cycle_nodes)

    return depth, cycle_nodes


def _fsm_depth_dfs(
    start: str,
    adjacency: dict[str, set[str]],
    color: dict[str, int],
    depth: dict[str, int],
    best: dict[str, int],
    cycle_nodes: set[str],
) -> None:
    """Iterative DFS from one unvisited *start*, filling the shared maps in place."""
    color[start] = _GRAY
    best[start] = 0
    stack: list[tuple[str, Iterator[str]]] = [(start, iter(adjacency[start]))]
    while stack:
        node, children = stack[-1]
        descended = False
        for child in children:
            child_color = color[child]
            if child_color == _GRAY:
                cycle_nodes.add(child)
            elif child_color == _BLACK:
                best[node] = max(best[node], depth[child])
            else:
                color[child] = _GRAY
                best[child] = 0
                stack.append((child, iter(adjacency[child])))
                descended = True
                break
        if descended:
            continue
        stack.pop()
        depth[node] = 1 + best[node]
        color[node] = _BLACK
        if stack:
            parent = stack[-1][0]
            best[parent] = max(best[parent], depth[node])


def _validate_fsm_depth(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    node_type: Any,
) -> list[ValidationCheck]:
    """Validate FSM nesting depth doesn't exceed MAX_FSM_DEPTH."""
    checks: list[ValidationCheck] = []
    port_map = {p["id"]: p for p in ports}
    fsm_nodes = {n["id"] for n in nodes if node_type(n["id"]) == "FSM"}

    if not fsm_nodes:
        return checks

    invoke_edges = [e for e in edges if e.get("kind", e.get("edge_kind", "")) == "INVOKE"]

    adjacency: dict[str, set[str]] = {fsm_id: set() for fsm_id in fsm_nodes}
    for edge in invoke_edges:
        source_port = port_map.get(edge.get("source_port_id", ""))
        if not source_port:
            continue
        source_node_id: Any = source_port.get("node_id")
        target_node_id = _invoke_target_node(edge, port_map, source_node_id)
        if source_node_id in fsm_nodes and target_node_id in fsm_nodes:
            adjacency[source_node_id].add(target_node_id)

    depth_by_node, cycle_nodes = _compute_fsm_depths(adjacency)
    for node_id in sorted(cycle_nodes):
        checks.append(
            ValidationCheck(
                id=f"ORCH-DEPTH-{len(checks) + 1:03d}",
                severity="error",
                category="graph",
                title="Circular FSM reference",
                message=f"Circular FSM reference detected involving node {node_id}.",
            )
        )

    depth = max(depth_by_node.values(), default=0)
    if depth > MAX_FSM_DEPTH:
        checks.append(
            ValidationCheck(
                id=f"ORCH-DEPTH-{len(checks) + 1:03d}",
                severity="error",
                category="graph",
                title="FSM nesting too deep",
                message=f"FSM nesting depth {depth} exceeds maximum of {MAX_FSM_DEPTH}.",
            )
        )

    return checks
