"""Per-edge structural rules the platform enforces on import.

- ``ORCH-DIR``: source and target direction.
- ``ORCH-FSM``: AGENT/FSM out ports.
- ``ORCH-BIDIR``: state/policy port pairing.
- ``ORCH-MFCONT``: who may continue the mainflow.

Two platform rules are not applied, because the platform stores the mainflow in
node config while an SDK project materializes the chain as MAINFLOW edges:

- Single edge per port. Counting MAINFLOW edges here would reject graphs the
  platform accepts.
- A declared AGENT/TOOL ``out`` port carrying the mainflow. This is the normal
  SDK spelling, and the bundle writer converts it into
  ``config.mainflow_target_node_ref`` before upload.

The writer converts TOOL nodes too, and the platform rejects the result, so
``ORCH-MFCONT`` checks who may continue the mainflow at all.
"""

from __future__ import annotations

from typing import Any, Callable

from ._constants import MAINFLOW_COMPONENT_TYPES, VALID_PORT_TYPES_BY_CATEGORY

# A finding: (check-id stem, title, message).
Finding = tuple[str, str, str]

# Mirrors perceivo._bundle.component._DIR_BY_PORT_TYPE, which is how the platform
# assigns a component port's direction when the manifest does not state one.
_DIR_BY_PORT_TYPE: dict[str, str] = {
    "external_in": "IN",
    "in": "IN",
    "out": "OUT",
    "tool": "OUT",
    "policy": "OUT",
    "state": "OUT",
    "review": "OUT",
    "external_out": "OUT",
}

# Port types that may source an edge despite an IN direction: they invoke a
# handler rather than carrying flow.
_INVOKE_PORT_TYPES = ("state", "policy", "tool")

# Component types whose IN port may source a MAINFLOW edge, the SDK spelling of a
# phantom mainflow pass-through.
_MAINFLOW_IN_SOURCES = ("AGENT", "FSM", "ADAPTER", "ORCHESTRATION")

# FSM/Agent OUT ports may only connect to FSM/Agent targets (stacking).
_STACKED_SOURCES = ("AGENT", "FSM")
_STACKED_TARGETS = frozenset({"AGENT", "FSM"})

# Bidirectional port types: these must connect to other bidirectional ports.
_BIDIRECTIONAL_PORT_TYPES = frozenset({"state", "policy"})

# Node types that own a phantom out-port and may therefore continue the mainflow.
# Shares ``_structure.py``'s constant so the two rules cannot drift.
_MAINFLOW_CAPABLE = frozenset(MAINFLOW_COMPONENT_TYPES)

# Node types whose declared ``out`` MAINFLOW edge the bundle writer rewrites into a
# config continuation; identical to the writer's ``_MAINFLOW_CONFIG_TYPES``.
_MAINFLOW_CONFIG_TYPES = frozenset({"AGENT", "TOOL"})

# The three spellings of a config continuation the platform accepts.
_MAINFLOW_CONFIG_KEYS = (
    "mainflow_target_node_ref",
    "mainflow_target_node_index",
    "mainflow_target_node_id",
)


def port_direction(port: dict[str, Any]) -> str:
    """The port's direction, derived from ``port_type`` when not stated.

    Mirrors the platform's own derivation so a port that omits ``direction``
    is judged the same way here as it is on import.
    """
    explicit = str(port.get("direction") or "").upper()
    if explicit in ("IN", "OUT", "BIDIR"):
        return explicit
    return _DIR_BY_PORT_TYPE.get(str(port.get("port_type") or "").lower(), "IN")


def _port_type(port: dict[str, Any]) -> str:
    return str(port.get("port_type") or "").lower()


def _edge_kind(edge: dict[str, Any]) -> str:
    """The edge's kind, tolerating both the ``kind`` and ``edge_kind`` spellings."""
    return str(edge.get("kind", edge.get("edge_kind", "MAINFLOW"))).upper().strip()


def _port_label(port: dict[str, Any]) -> str:
    return str(port.get("key") or port.get("name") or port.get("id") or "?")


def _direction_findings(
    source: dict[str, Any],
    target: dict[str, Any],
    source_type: str,
    is_mainflow: bool,
) -> list[Finding]:
    """Source must be OUT/BIDIR (or an invoke port); target must be IN/BIDIR."""
    findings: list[Finding] = []

    src_dir = port_direction(source)
    src_type = _port_type(source)

    source_ok = (
        src_dir in ("OUT", "BIDIR")
        or src_type in _INVOKE_PORT_TYPES
        or (is_mainflow and src_dir == "IN" and source_type in _MAINFLOW_IN_SOURCES)
    )
    if not source_ok:
        findings.append(
            (
                "ORCH-DIR",
                "Invalid edge source direction",
                f"Source port '{_port_label(source)}' must be OUT, bidirectional, or an "
                f"invoke port (state/policy/tool): direction={src_dir}, "
                f"port_type={src_type or 'unset'}.",
            )
        )

    tgt_dir = port_direction(target)
    if tgt_dir not in ("IN", "BIDIR"):
        findings.append(
            (
                "ORCH-DIR",
                "Invalid edge target direction",
                f"Target port '{_port_label(target)}' must be IN or bidirectional: "
                f"direction={tgt_dir}.",
            )
        )
    return findings


def _stacked_finding(
    source: dict[str, Any],
    source_type: str,
    target_type: str,
    is_mainflow: bool,
) -> Finding | None:
    """An AGENT/FSM ``out`` port may only reach AGENT/FSM."""
    if (
        source_type in _STACKED_SOURCES
        and _port_type(source) == "out"
        and not is_mainflow
        and target_type not in _STACKED_TARGETS
    ):
        return (
            "ORCH-FSM",
            "Invalid FSM connection",
            f"{source_type} OUT ports can only connect to AGENT or FSM components "
            f"(found target: {target_type or 'unknown'}). Use INVOKE edges for "
            f"tool/policy/review connections.",
        )
    return None


def _continued_nodes(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_type: Callable[[str], str],
) -> dict[str, str]:
    """Node id -> how it declares a mainflow continuation, config spelling first."""
    port_map = {str(p.get("id")): p for p in ports}

    continued: dict[str, str] = {}
    for node in nodes:
        node_id = str(node.get("id", ""))
        config = node.get("config") or {}
        if any(config.get(key) for key in _MAINFLOW_CONFIG_KEYS):
            continued[node_id] = "config.mainflow_target_node_ref"

    for edge in edges:
        if _edge_kind(edge) != "MAINFLOW":
            continue
        source = port_map.get(str(edge.get("source_port_id", "")))
        if source is None or source.get("virtual"):
            continue
        if _port_type(source) != "out":
            continue
        node_id = str(source.get("node_id", ""))
        if node_type(node_id) in _MAINFLOW_CONFIG_TYPES:
            continued.setdefault(node_id, f"its declared '{_port_label(source)}' out port")
    return continued


def mainflow_continuation_findings(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_type: Callable[[str], str],
) -> list[Finding]:
    """Report nodes that continue the mainflow without being mainflow-capable.

    A node continues the mainflow through a *phantom* out port derived from its
    in port, and only ``AGENT``/``FSM``/``ADAPTER``/``ORCHESTRATION``
    have one. A ``TOOL`` does not: it is invoked and returns through the port it
    was called on, so it has no continuation to give.

    Both spellings count, because the bundle writer turns the second into the
    first:

    1. ``config.mainflow_target_node_*`` on the node, and
    2. a declared ``out`` port on an AGENT/TOOL sourcing a MAINFLOW edge.

    The writer converts spelling 2 for AGENT *and* TOOL, but the platform accepts
    the result only for AGENT, so a TOOL on the mainflow exports cleanly and is
    then rejected on import.

    Virtual ports are ignored: a synthesized continuation is the *result* of a
    config hop already counted under spelling 1, so honoring it would double-report.
    """
    findings: list[Finding] = []
    continued = _continued_nodes(nodes, ports, edges, node_type)

    for node_id, how in continued.items():
        ntype = node_type(node_id)
        if ntype in _MAINFLOW_CAPABLE or not ntype:
            continue
        allowed = ", ".join(sorted(_MAINFLOW_CAPABLE))
        findings.append(
            (
                "ORCH-MFCONT",
                "Component cannot continue the mainflow",
                f"Node '{node_id}' declares a mainflow continuation (via {how}) but its "
                f"type '{ntype}' has no phantom out-port. Only {allowed} components "
                f"continue the mainflow through a converted in-port. REMEDY: a {ntype} is "
                f"invoked, not chained -- give the work to an AGENT on the mainflow and "
                f"have it reach this node over an INVOKE edge from a 'tool' port.",
            )
        )
    return findings


def _bidir_findings(
    source: dict[str, Any],
    target: dict[str, Any],
    edge_kind: str,
) -> list[Finding]:
    """``state``/``policy`` ports must pair with ``state``/``policy`` ports.

    INVOKE edges are exempt: an FSM state or policy port dispatches to a handler
    whose IN port is unidirectional, which is the normal INVOKE pattern.
    """
    if edge_kind == "INVOKE":
        return []

    source_is_bidir = _port_type(source) in _BIDIRECTIONAL_PORT_TYPES
    target_is_bidir = _port_type(target) in _BIDIRECTIONAL_PORT_TYPES
    if source_is_bidir == target_is_bidir:
        return []

    if source_is_bidir:
        message = (
            f"Port type mismatch: bidirectional port '{_port_label(source)}' cannot "
            f"connect to unidirectional port '{_port_label(target)}'."
        )
    else:
        message = (
            f"Port type mismatch: unidirectional port '{_port_label(source)}' cannot "
            f"connect to bidirectional port '{_port_label(target)}'."
        )
    return [("ORCH-BIDIR", "Port type mismatch", message)]


def edge_rule_findings(
    edge: dict[str, Any],
    port_map: dict[str, dict[str, Any]],
    node_type: Callable[[str], str],
) -> list[Finding]:
    """Return every structural finding for one edge, in the platform's order."""
    source = port_map.get(str(edge.get("source_port_id", "")))
    target = port_map.get(str(edge.get("target_port_id", "")))
    if not source or not target:
        return []

    edge_kind = str(edge.get("kind", edge.get("edge_kind", "MAINFLOW"))).upper()
    is_mainflow = edge_kind == "MAINFLOW"
    source_type = node_type(str(source.get("node_id", "")))
    target_type = node_type(str(target.get("node_id", "")))

    findings = _direction_findings(source, target, source_type, is_mainflow)

    layered = _stacked_finding(source, source_type, target_type, is_mainflow)
    if layered:
        findings.append(layered)

    findings.extend(_bidir_findings(source, target, edge_kind))
    return findings


def port_type_findings(
    ports: list[dict[str, Any]],
    node_type: Callable[[str], str],
) -> list[Finding]:
    """Report ports whose ``port_type`` is illegal for their node's component category.

    The legal types come from ``VALID_PORT_TYPES_BY_CATEGORY`` in ``_constants``.
    """
    findings: list[Finding] = []
    for port in ports:
        ptype = _port_type(port)
        if not ptype:
            continue
        category = node_type(str(port.get("node_id", "")))
        allowed = VALID_PORT_TYPES_BY_CATEGORY.get(category)
        if not allowed or ptype in allowed:
            continue
        findings.append(
            (
                "ORCH-PORTTYPE",
                "Invalid port type for component",
                f"Invalid port_type '{ptype}' on {category} component "
                f"(port '{_port_label(port)}'). {category} allows: {sorted(allowed)}.",
            )
        )
    return findings
