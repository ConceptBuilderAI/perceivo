"""Port-level checks: single edge per port, all ports connected, FSM-policy wiring."""

from __future__ import annotations

from typing import Any

from .._report import ValidationCheck
from ._constants import MAINFLOW_EDGE_KINDS


def _edge_kind(edge: dict[str, Any]) -> str:
    """Return the normalized edge kind (``MAINFLOW`` / ``INVOKE`` / ...)."""
    return str(edge.get("kind", edge.get("edge_kind", "MAINFLOW"))).upper()


def _validate_single_edge_per_port(
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    source_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}

    for edge in edges:
        # MAINFLOW edges are node config on the platform, so they never count toward the
        # per-port cap; only INVOKE edges are capped.
        if _edge_kind(edge) in MAINFLOW_EDGE_KINDS:
            continue
        src = edge.get("source_port_id", "")
        tgt = edge.get("target_port_id", "")
        source_counts[src] = source_counts.get(src, 0) + 1
        target_counts[tgt] = target_counts.get(tgt, 0) + 1

    port_names = {p["id"]: p.get("name", p["id"]) for p in ports}

    for port_id, count in source_counts.items():
        if count > 1:
            checks.append(
                ValidationCheck(
                    id=f"ORCH-MULTI-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="Multiple outgoing edges",
                    message=f"Port '{port_names.get(port_id, port_id)}' has {count} outgoing edges (max 1).",
                )
            )

    for port_id, count in target_counts.items():
        if count > 1:
            checks.append(
                ValidationCheck(
                    id=f"ORCH-MULTI-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="Multiple incoming edges",
                    message=f"Port '{port_names.get(port_id, port_id)}' has {count} incoming edges (max 1).",
                )
            )

    # Combined cap: a port may carry at most one non-mainflow edge in total, across
    # both directions.
    for port_id in set(source_counts) & set(target_counts):
        s = source_counts[port_id]
        t = target_counts[port_id]
        if s <= 1 and t <= 1 and s + t > 1:
            checks.append(
                ValidationCheck(
                    id=f"ORCH-MULTI-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="Multiple edges on port",
                    message=f"Port '{port_names.get(port_id, port_id)}' participates in "
                    f"{s + t} edges ({s} outgoing + {t} incoming); a port may carry at most 1.",
                )
            )

    return checks


def _positional_node_id(target: Any, key_by_index: dict[int, str]) -> str | None:
    """The id of the node at position *target*, for the positional spellings.

    Accepts ``N``, ``"N"`` and ``"node_N"``, the forms the platform writes for
    a reference that means a position. Anything else resolves to nothing, which
    is the safe answer: an unresolvable target must exempt no node.
    """
    ref = str(target)
    if ref.startswith("node_"):
        ref = ref[len("node_") :]
    try:
        return key_by_index.get(int(ref))
    except (TypeError, ValueError):
        return None


def _config_mainflow_targets(config: dict[str, Any], key_by_index: dict[int, str]) -> set[str]:
    """The mainflow targets one node's config names, in every spelling.

    See :func:`_unwired_exempt_nodes` for why only the positional spellings are
    resolved positionally.
    """
    targets: set[str] = set()
    for key in (
        "mainflow_target_node_ref",
        "mainflow_target_node_id",
        "mainflow_target_node_index",
    ):
        target = config.get(key)
        # Presence, not truthiness: on import these can be node INDICES and
        # index 0 is a legitimate target.
        if target is None or target == "":
            continue
        targets.add(str(target))
        if key != "mainflow_target_node_id":
            resolved = _positional_node_id(target, key_by_index)
            if resolved is not None:
                targets.add(resolved)
    return targets


def _unwired_exempt_nodes(nodes: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    """Return node ids whose unconnected ports the platform tolerates.

    - mainflow targets: a node another node names via
      ``config.mainflow_target_node_ref`` / ``mainflow_target_node_id`` /
      ``mainflow_target_node_index``. The hop is expressed in config, not as an
      edge, so the target's IN port is legitimately unwired.
    - combo agents: ``config.is_agent_tool`` nodes, whose tool-type IN port is
      wired by an engine-managed INVOKE the bundle does not carry.
    - hidden nodes: ``config.is_agent_tool_internal``, the paired bundled tool.
      Every port on one is engine-managed.

    All three mainflow spellings must be read. The canvas writes
    ``mainflow_target_node_index`` for every phantom hop and
    ``mainflow_target_node_id`` only when the target already has a UUID, which it
    does not on the request that creates the orchestration. An orchestration built
    in the UI and never re-saved therefore carries the index spelling alone.

    **Only the positional spellings are resolved positionally.** Every entry added
    here suppresses the check this module exists to run, so an entry added by
    mistake is a silent hole rather than a visible error.

    ``_ref`` (``node_N``) and ``_index`` (``N``) both *mean* "the node at
    position N", so mapping them through the node list cannot name a node the
    author did not. ``_id`` is an identifier (a UUID on a live graph, a small
    integer on a bootstrap bundle) and is matched raw. Reading it as a position
    would let a node exempt itself: for ids ``["node_0", "node_1"]`` and
    ``node_1`` carrying ``mainflow_target_node_id: 1``, position 1 *is* the node
    holding the config. An id that matches no node must exempt nothing.
    """
    node_list = nodes or []
    key_by_index = {i: str(n.get("id", "")) for i, n in enumerate(node_list)}
    mainflow_targets: set[str] = set()
    combo_agents: set[str] = set()
    hidden: set[str] = set()
    for node in node_list:
        config = node.get("config") or {}
        mainflow_targets |= _config_mainflow_targets(config, key_by_index)
        node_id = str(node.get("id", ""))
        if config.get("is_agent_tool"):
            combo_agents.add(node_id)
        if config.get("is_agent_tool_internal"):
            hidden.add(node_id)
    return mainflow_targets, combo_agents, hidden


def _unwired_port_exempt(
    port: dict[str, Any],
    ptype: str,
    ntype: str,
    node_id: str,
    exempt: tuple[set[str], set[str], set[str]],
) -> bool:
    """Return whether the platform tolerates *port* being unwired.

    *exempt* is ``(mainflow_targets, combo_agents, hidden)`` as returned by
    :func:`_unwired_exempt_nodes`.
    """
    mainflow_targets, combo_agents, hidden = exempt
    if ptype == "external_in" and ntype == "START":
        return True
    if ptype == "external_out" and ntype == "END":
        return True
    if node_id in hidden:
        return True
    if port.get("direction", "").upper() == "IN" or ptype == "in":
        if node_id in mainflow_targets:
            return True
        if ptype == "tool" and node_id in combo_agents:
            return True
    return False


def _validate_all_ports_connected(
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_type: Any,
    nodes: list[dict[str, Any]] | None = None,
) -> list[ValidationCheck]:
    """Check that every port is wired, bar the exemptions the platform also makes.

    A node on the mainflow does not need a declared ``out`` port: being on the
    mainflow splits its bidirectional ``in`` port into an in port and a phantom out
    port, and the phantom carries the continuation. An INVOKE target returns
    through the same in port. In both cases a declared ``out`` port has nothing it
    could legally connect to, so an unwired one is reported.

    Exempt: START's ``external_in``, END's ``external_out``, the IN port of a node
    named as a mainflow target, the tool-type IN port of a combo agent, and every
    port on a hidden bundled-tool node. See ``_unwired_exempt_nodes``.
    """
    checks: list[ValidationCheck] = []
    connected_ports: set[str] = set()
    mainflow_targets, combo_agents, hidden = _unwired_exempt_nodes(nodes or [])

    for edge in edges:
        connected_ports.add(edge.get("source_port_id", ""))
        connected_ports.add(edge.get("target_port_id", ""))

    for port in ports:
        port_id = port["id"]
        if port_id in connected_ports:
            continue

        ptype = port.get("port_type", "")
        node_id = str(port.get("node_id", ""))
        ntype = node_type(port.get("node_id", ""))

        if _unwired_port_exempt(
            port, ptype, ntype, node_id, (mainflow_targets, combo_agents, hidden)
        ):
            continue

        checks.append(
            ValidationCheck(
                id=f"ORCH-UNWIRED-{len(checks) + 1:03d}",
                severity="error",
                category="graph",
                title="Unconnected port",
                message=f"Port '{port.get('name', port_id)}' on {ntype} node is not "
                f"connected. Wire it, or remove it from the component if it has "
                f"nothing to connect to.",
            )
        )

    return checks


def _policy_port_connected(
    policy_port_id: str,
    edges: list[dict[str, Any]],
    port_map: dict[str, dict[str, Any]],
    node_type: Any,
) -> bool:
    """Return True if the policy port has an INVOKE edge into a POLICY component.

    Only an INVOKE edge wires a policy port to its POLICY component; a MAINFLOW
    edge does not satisfy the requirement.
    """
    for edge in edges:
        if edge.get("source_port_id") != policy_port_id:
            continue
        if _edge_kind(edge) != "INVOKE":
            continue
        target_port = port_map.get(edge.get("target_port_id", ""))
        if target_port and node_type(target_port.get("node_id", "")) == "POLICY":
            return True
    return False


def _check_fsm_policy(
    fsm: dict[str, Any],
    ports_by_node: dict[str, list[dict[str, Any]]],
    edges: list[dict[str, Any]],
    port_map: dict[str, dict[str, Any]],
    node_type: Any,
    checks: list[ValidationCheck],
) -> None:
    """Append policy-wiring checks for a single FSM node (in place)."""
    fsm_id = fsm["id"]
    policy_ports = [p for p in ports_by_node.get(fsm_id, []) if p.get("port_type") == "policy"]

    if not policy_ports:
        checks.append(
            ValidationCheck(
                id=f"ORCH-FSM-{len(checks) + 1:03d}",
                severity="error",
                category="graph",
                title="FSM missing policy",
                message=f"FSM node '{fsm.get('name', fsm_id)}' has no policy port.",
            )
        )
        return

    for pp in policy_ports:
        if not _policy_port_connected(pp["id"], edges, port_map, node_type):
            checks.append(
                ValidationCheck(
                    id=f"ORCH-FSM-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="FSM policy not connected",
                    message=f"FSM node '{fsm.get('name', fsm_id)}' policy port is not "
                    f"connected to a POLICY component.",
                )
            )


def _validate_fsm_requires_policy(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_type: Any,
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    port_map = {p["id"]: p for p in ports}
    ports_by_node: dict[str, list[dict[str, Any]]] = {}
    for p in ports:
        ports_by_node.setdefault(p.get("node_id", ""), []).append(p)

    fsm_nodes = [n for n in nodes if node_type(n["id"]) == "FSM"]

    for fsm in fsm_nodes:
        _check_fsm_policy(fsm, ports_by_node, edges, port_map, node_type, checks)

    return checks


def _state_port_has_handler(state_port_id: str, edges: list[dict[str, Any]]) -> bool:
    """Return True if the state port has at least one outgoing INVOKE edge."""
    for edge in edges:
        if edge.get("source_port_id") != state_port_id:
            continue
        if _edge_kind(edge) == "INVOKE":
            return True
    return False


def _validate_fsm_state_ports_connected(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_type: Any,
) -> list[ValidationCheck]:
    """Check that each FSM state port is wired to a handler via an INVOKE edge.

    A state port with no outgoing INVOKE edge is a dead end: the FSM can
    transition into that state but has no handler to execute at runtime.
    """
    checks: list[ValidationCheck] = []
    ports_by_node: dict[str, list[dict[str, Any]]] = {}
    for p in ports:
        ports_by_node.setdefault(p.get("node_id", ""), []).append(p)

    fsm_nodes = [n for n in nodes if node_type(n["id"]) == "FSM"]

    for fsm in fsm_nodes:
        fsm_id = fsm["id"]
        for port in ports_by_node.get(fsm_id, []):
            if port.get("port_type") != "state":
                continue
            if _state_port_has_handler(port["id"], edges):
                continue
            checks.append(
                ValidationCheck(
                    id=f"ORCH-FSM-STATE-{len(checks) + 1:03d}",
                    severity="error",
                    category="graph",
                    title="FSM state port has no handler",
                    message=f"FSM state port '{port.get('name', port['id'])}' on node "
                    f"'{fsm.get('name', fsm_id)}' has no handler connected via an INVOKE edge.",
                )
            )

    return checks


#: Component port types that are invoke slots: the node calls whatever the slot
#: is wired to, so a slot the graph never lists has nothing to call.
_UNLISTED_SLOT_TYPES = frozenset({"state", "policy"})


def _validate_declared_slots_listed(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    node_type: Any,
    bundled_ports: dict[str, list[dict[str, Any]]] | None = None,
) -> list[ValidationCheck]:
    """Warn on a component's invoke slot that the graph does not list for its node.

    The component declares the slot (in its manifest, or in the node's pinned
    ``component_ports``), so the platform creates it, but with no port row there
    is no edge either, and the unconnected-port and FSM-state checks never see
    it. The orchestration runs until the node selects that slot, then fails
    because nothing is wired to it. A template may ship slots unwired on
    purpose, so this is a warning rather than an error.
    """
    checks: list[ValidationCheck] = []
    listed: dict[str, set[str]] = {}
    for port in ports:
        key = str(port.get("port_name") or port.get("key") or port.get("name") or "")
        listed.setdefault(str(port.get("node_id", "")), set()).add(key)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        declared = (bundled_ports or {}).get(node_id)
        if declared is None:
            pinned = node.get("component_ports")
            declared = pinned if isinstance(pinned, list) else []
        for port in declared:
            if not isinstance(port, dict):
                continue
            if str(port.get("port_type") or "").lower() not in _UNLISTED_SLOT_TYPES:
                continue
            if str(port.get("direction") or "").upper() != "OUT":
                continue
            key = str(port.get("key") or port.get("name") or "")
            if not key or key in listed.get(node_id, set()):
                continue
            checks.append(
                ValidationCheck(
                    id=f"ORCH-UNLISTED-SLOT-{len(checks) + 1:03d}",
                    severity="warning",
                    category="graph",
                    title="Component slot not in the graph",
                    message=f"{node_type(node_id) or 'Component'} node "
                    f"'{node.get('name', node_id)}' declares {port.get('port_type')} slot "
                    f"'{key}', but the graph has no port for it, so nothing is wired to it. "
                    f"A run that selects '{key}' fails. Add the port and wire a handler "
                    f"to it, or remove the slot from the component.",
                )
            )
    return checks
