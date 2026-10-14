"""Every component port in the bundle ``publish`` sends must carry an edge.

The platform importer creates an orchestration port for every port a bundled
component declares, including ports the bundle's own ``ports`` list omits, and
then refuses any that no edge connects: "Port 'output' on node 'agent' has no
connected edge". The file checks judge ``orchestration.yaml``, where the same
port may be wired by an edge the writer later translates into node config, so a
project that passes locally can still be refused on upload.

This applies the platform's rule to the bundle itself. The exempt node sets
come from :func:`._ports._unwired_exempt_nodes`, the same derivation the file
check uses, keyed on the bundle's ``local_id`` and node order, so the two checks
cannot drift apart. A port is exempt when it is:

- an ``external_in`` / ``external_out`` port;
- any port on a hidden node (``config.is_agent_tool_internal``);
- an IN port on a mainflow target, named by any of
  ``mainflow_target_node_ref`` / ``_index`` (both positional) or ``_id`` (raw);
- a ``tool`` IN port on a node whose config sets ``is_agent_tool``.

No other OUT port is exempt. A ``state`` or ``policy`` port the bundle carries no
row for is not judged: the importer does not create a row for those two types
(its FSM checks own their wiring). Nested-orchestration nodes are skipped: the
platform generates their ports from the child.
"""

from __future__ import annotations

from typing import Any

from .._report import ValidationCheck
from ._ports import _unwired_exempt_nodes

_EXTERNAL = ("external_in", "external_out")

#: Port types the importer never creates a missing orchestration row for.
_NOT_INJECTED = ("state", "policy")


def _row_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("node_ref")),
        str(row.get("component_port_key")),
        str(row.get("component_port_direction")).upper(),
    )


def _connected(bundle: dict[str, Any]) -> set[tuple[str, str, str]]:
    """``(node_ref, key, direction)`` of every port row some edge references."""
    rows = bundle.get("ports") or []
    connected: set[tuple[str, str, str]] = set()
    for edge in bundle.get("edges") or []:
        for index in (edge.get("source_port_index"), edge.get("target_port_index")):
            if isinstance(index, int) and 0 <= index < len(rows):
                connected.add(_row_identity(rows[index]))
    return connected


def _exempt(port: dict[str, Any], node_id: str, exempt: tuple[set[str], ...]) -> bool:
    targets, combo_agents, hidden = exempt
    port_type = str(port.get("port_type") or "").lower()
    if port_type in _EXTERNAL or node_id in hidden:
        return True
    if str(port.get("direction") or "").upper() != "IN":
        return False
    if node_id in targets:
        return True
    return port_type == "tool" and node_id in combo_agents


def unconnected_port_checks(bundle: dict[str, Any]) -> list[ValidationCheck]:
    """One error per bundled component port the platform would find unconnected."""
    components = bundle.get("components") or {}
    nodes = bundle.get("nodes") or []
    exempt = _unwired_exempt_nodes(
        [{"id": str(node.get("local_id")), "config": node.get("config")} for node in nodes]
    )
    connected = _connected(bundle)
    rows = {_row_identity(row) for row in bundle.get("ports") or []}
    checks: list[ValidationCheck] = []
    for node in nodes:
        component = components.get(str(node.get("component_ref") or ""))
        if component is None or node.get("orchestration_ref"):
            continue
        local_id = str(node.get("local_id"))
        for port in component.get("ports") or []:
            key = str(port.get("key"))
            identity = (local_id, key, str(port.get("direction") or "").upper())
            if identity in connected or _exempt(port, local_id, exempt):
                continue
            if identity not in rows and str(port.get("port_type") or "").lower() in _NOT_INJECTED:
                continue
            checks.append(
                ValidationCheck(
                    id=f"ORCH-BUNDLE-UNCONNECTED-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="Bundled port has no connected edge",
                    message=(
                        f"Port '{key}' on node '{node.get('name') or local_id}' has no "
                        "connected edge in the bundle publish sends, and the platform "
                        "refuses an unconnected port. Connect it with an edge in "
                        "orchestration.yaml, or remove it from the component if it has "
                        "nothing to connect to."
                    ),
                )
            )
    return checks
