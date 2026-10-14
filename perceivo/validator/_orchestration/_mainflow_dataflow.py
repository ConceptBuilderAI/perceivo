"""Compute effective response contracts along an SDK mainflow chain."""

from __future__ import annotations

from typing import Any

from ._constants import MAINFLOW_EDGE_KINDS

PASSTHROUGH_SOURCE = "request_data"


def _downstream_contract_port(node_id: str, ports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the port carrying what *node_id* sends to its mainflow successor.

    A ``virtual`` port, synthesized for a config-declared mainflow hop, is skipped:
    it has no component port behind it and carries no contract, and preferring it
    would make every config hop look like it produced nothing.
    """
    in_port = None
    for port in ports:
        if str(port.get("node_id")) != node_id or port.get("virtual"):
            continue
        direction = str(port.get("direction") or "").lower()
        port_type = str(port.get("port_type") or "").lower()
        if direction == "out" and port_type == "out":
            return port
        if direction == "in" and port_type == "in":
            in_port = port
    return in_port


def with_effective_contract(port: dict[str, Any], ports: list[dict[str, Any]]) -> dict[str, Any]:
    """Return *port* as the platform's contract check reads it.

    Each payload schema that the port's ``effective_contract`` sets to a non-null
    value overrides the component port's; an unset or null half falls back to the
    component schema. A ``virtual`` out port has no orchestration port of its own,
    so it reads the contract of the node's real downstream port, which is the port
    the platform checks a config-declared hop against.
    """
    owner = port
    if port.get("virtual") and str(port.get("direction") or "").lower() == "out":
        owner = _downstream_contract_port(str(port.get("node_id")), ports) or {}
    contract = owner.get("effective_contract")
    if not isinstance(contract, dict):
        return port
    overrides = {
        key: contract[key]
        for key in ("request_data_schema", "response_data_schema")
        if contract.get(key) is not None
    }
    return {**port, **overrides} if overrides else port


def _declares_passthrough(port: dict[str, Any]) -> bool:
    value = port.get("response_data_passthrough")
    return str(value or "").strip().lower() == PASSTHROUGH_SOURCE


def _passthrough_exceptions(port: dict[str, Any]) -> set[str]:
    raw = port.get("response_data_passthrough_except") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return set()
    return {str(value).strip() for value in raw if str(value).strip()}


def _successors(edges: list[dict[str, Any]], port_map: dict[str, dict[str, Any]]) -> dict[str, str]:
    successors: dict[str, str] = {}
    for edge in edges:
        kind = str(edge.get("kind", edge.get("edge_kind", "MAINFLOW"))).upper()
        if kind not in MAINFLOW_EDGE_KINDS:
            continue
        source = port_map.get(str(edge.get("source_port_id", "")))
        target = port_map.get(str(edge.get("target_port_id", "")))
        if source and target:
            successors.setdefault(str(source.get("node_id")), str(target.get("node_id")))
    return successors


def _node_response_fields(
    node_id: str,
    ports: list[dict[str, Any]],
    arriving: dict[str, Any],
) -> object:
    port = _downstream_contract_port(node_id, ports)
    if port is None:
        return dict(arriving)

    declared = port.get("response_data_schema")
    if declared is not None and not isinstance(declared, dict):
        # Preserve malformed declarations for the contract validator to reject.
        return declared
    output = dict(declared or {})
    if _declares_passthrough(port):
        exceptions = _passthrough_exceptions(port)
        for name, schema in arriving.items():
            if name not in exceptions and name not in output:
                output[name] = schema
    return output


def _walk_chain(
    head: str,
    successors: dict[str, str],
    node_ids: set[str],
    ports: list[dict[str, Any]],
    provided: dict[str, Any],
    arriving: dict[str, dict[str, Any]],
) -> None:
    current: str | None = head
    seen: set[str] = set()
    while current is not None and current not in seen and current in node_ids:
        seen.add(current)
        provided[current] = _node_response_fields(current, ports, dict(arriving.get(current) or {}))
        successor = successors.get(current)
        if successor is not None:
            value = provided[current]
            arriving[successor] = dict(value) if isinstance(value, dict) else {}
        current = successor


def compute_available_response_fields(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map node ids to the response fields they actually send downstream."""
    node_ids = {str(node.get("id")) for node in nodes if node.get("id") is not None}
    port_map = {str(port.get("id")): port for port in ports if port.get("id") is not None}
    successors = _successors(edges, port_map)
    targets = set(successors.values())
    heads = [node_id for node_id in node_ids if node_id not in targets]

    provided: dict[str, Any] = {}
    arriving: dict[str, dict[str, Any]] = {}
    for head in heads:
        _walk_chain(head, successors, node_ids, ports, provided, arriving)
    return provided
