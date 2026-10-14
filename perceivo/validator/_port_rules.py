"""Port count and type rules, and the manifest reader that feeds them.

Every budget in :data:`_PORT_BUDGETS` matches the platform's port rules.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ._create_ports import create_refusal
from ._report import ValidationCheck


def _rule_group(port: dict[str, Any]) -> str:
    """Return the rule group a port counts against.

    The platform computes it as::

        raw_type = portType or port_type or direction or "in"
        pt = raw_type.strip().lower()

    Type first, direction only as the fallback. Tool, policy, state and review
    ports are all OUT-*direction*, because each one is an INVOKE source, so
    grouping on direction would count them as data OUT ports and report an agent
    with sixteen tools as having seventeen outputs.

    Every group the platform budgets is kept in its own bucket (``tool``,
    ``review``, ``external_in`` and ``external_out`` included), which keeps them
    out of the in/out counts while still counting them. A port whose type is
    outside :data:`_RULE_GROUPS` is returned as itself and dropped by the caller,
    because the platform has no budget to judge it against.

    Case-folding here means a lowercase ``direction`` is not a count error;
    :func:`_direction_findings` reports it directly.
    """
    raw = port.get("portType") or port.get("port_type") or port.get("direction") or "in"
    return str(raw).strip().lower()


#: The only two directions the platform stores.
_CANONICAL_DIRECTIONS = ("IN", "OUT")


def _direction_findings(ports: list[dict[str, Any]], add: Callable[[str], None]) -> None:
    """Report any port whose ``direction`` is not exactly IN or OUT.

    A sectioned manifest never trips this: the section fixes the direction, so the
    reader always emits a canonical one. It fires on an explicit port list where
    the author writes the direction by hand.

    The platform rejects a lowercase direction in a create payload, while the SDK
    bundler normalises it on the way out, so the author would never learn they
    wrote it. It is reported on both paths: a manifest that validates only because
    something downstream rewrote it has stopped describing its component.
    """
    for port in ports:
        direction = port.get("direction")
        if direction is None or direction in _CANONICAL_DIRECTIONS:
            continue
        name = port.get("key") or port.get("port_name") or port.get("name") or "?"
        add(
            f"Port '{name}' declares direction {direction!r}; the platform "
            f"accepts only 'IN' or 'OUT'"
        )


def _as_bundled(ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*ports* typed and directed as publish bundles them, which is what the platform counts.

    A port with no ``port_type`` is bundled as ``in`` or ``out`` by its direction.
    """
    from perceivo._bundle.component import _port_direction

    bundled = []
    for port in ports:
        direction = _port_direction(port)
        default = "in" if direction == "IN" else "out"
        bundled.append(
            {"port_type": str(port.get("port_type") or "") or default, "direction": direction}
        )
    return bundled


def validate_port_rules(
    component_type: str,
    ports: list[dict[str, Any]],
    fsm_states: Optional[list[str]] = None,
) -> list[ValidationCheck]:
    """Validate port count and type rules for a component.

    Args:
        component_type: The component type (AGENT, TOOL, FSM, etc.)
        ports: List of port dicts from the manifest.
        fsm_states: Optional list of FSM state names (for FSM components).

    Returns:
        List of validation checks (errors if rules violated).

    A component is first judged as the platform judges it when it creates the
    component (:func:`._create_ports.create_refusal`). That refusal is the
    platform's answer on its own, so it is the only count finding; the budgets
    run when it passes.
    """
    checks: list[ValidationCheck] = []
    ctype = component_type.upper().strip()

    groups: dict[str, list[dict[str, Any]]] = {group: [] for group in _RULE_GROUPS}
    for port in ports:
        group = _rule_group(port)
        if group in groups:
            groups[group].append(port)

    def _add(msg: str) -> None:
        checks.append(
            ValidationCheck(
                id=f"PORT-RULE-{len(checks) + 1:03d}",
                severity="error",
                category="interface",
                title="Port rule violation",
                message=msg,
            )
        )

    # Unconditional: a non-canonical direction is a finding whatever the
    # component type is, including the types with no count handler.
    _direction_findings(ports, _add)

    refusal = create_refusal(ctype, _as_bundled(ports))
    if refusal is not None:
        _add(refusal.message)
        return checks

    budgets = _PORT_BUDGETS.get(ctype)
    if budgets is not None:
        _budget_findings(ctype, budgets, groups, _add)
        if ctype == "FSM":
            _fsm_state_findings(groups, fsm_states, _add)

    return checks


#: The eight groups the platform budgets. Every port lands in exactly one of
#: them via :func:`_rule_group`.
_RULE_GROUPS = (
    "in",
    "out",
    "tool",
    "policy",
    "state",
    "review",
    "external_in",
    "external_out",
)

#: Port budgets, matching the platform's exactly; a type with no entry is unbudgeted.
#: An ``int`` is an exact count; a ``(low, high)`` tuple is an inclusive range.
#: The table is literal on purpose: a missing budget raises ``KeyError`` in
#: :func:`_budget_findings` rather than silently passing.
_PORT_BUDGETS: dict[str, dict[str, int | tuple[int, int]]] = {
    "AGENT": {
        "in": 1,
        "out": (0, 1),
        "tool": (0, 50),
        "review": (0, 1),
        "state": 0,
        "policy": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "TOOL": {
        "in": 1,
        "out": 0,
        "tool": (0, 50),
        "state": 0,
        "policy": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "FSM": {
        "in": 1,
        "policy": 1,
        "state": (0, 50),
        "out": 0,
        "tool": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "POLICY": {
        "in": 1,
        "out": 0,
        "tool": (0, 50),
        "state": 0,
        "policy": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "START": {
        "external_in": 1,
        "out": 1,
        "in": 0,
        "tool": 0,
        "policy": 0,
        "state": 0,
        "review": 0,
        "external_out": 0,
    },
    "END": {
        "in": 1,
        "external_out": 1,
        "out": 0,
        "external_in": 0,
        "tool": 0,
        "policy": 0,
        "state": 0,
        "review": 0,
    },
    "REVIEW": {
        "in": (0, 1),
        "review": (0, 1),
        "out": (0, 1),
        "tool": (0, 50),
        "state": 0,
        "policy": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "ADAPTER": {
        "in": 1,
        "state": (0, 50),
        "out": 0,
        "tool": 0,
        "policy": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
}

#: Why a budget of zero is zero, for the cases where the count alone does not
#: tell an author what to do instead. Appended to the message.
_ZERO_BUDGET_REASON: dict[tuple[str, str], str] = {
    ("TOOL", "out"): " (tools don't return artifacts)",
    ("AGENT", "policy"): " (only FSM can)",
    ("START", "in"): " (a START is entered through its EXTERNAL_IN port)",
    ("END", "out"): " (an END returns through its EXTERNAL_OUT port)",
    ("ADAPTER", "out"): " (an ADAPTER writes through its STATE ports)",
    ("FSM", "out"): " (an FSM's result leaves through FsmOutput.response_data)",
}


def _ports(count: int) -> str:
    return "port" if count == 1 else "ports"


def _budget_findings(
    ctype: str,
    budgets: dict[str, int | tuple[int, int]],
    groups: dict[str, list[dict[str, Any]]],
    add: Callable[[str], None],
) -> None:
    """Report every group whose port count falls outside its budget."""
    for group in _RULE_GROUPS:
        budget = budgets[group]
        found = len(groups[group])
        label = group.upper()
        if isinstance(budget, tuple):
            low, high = budget
            if found < low:
                add(f"{ctype} must have at least {low} {label} {_ports(low)} (found {found})")
            elif found > high:
                add(f"{ctype} can have at most {high} {label} {_ports(high)} (found {found})")
        elif found != budget:
            if budget == 0:
                reason = _ZERO_BUDGET_REASON.get((ctype, group), "")
                add(f"{ctype} cannot have {label} ports{reason} (found {found})")
            else:
                add(f"{ctype} must have exactly {budget} {label} {_ports(budget)} (found {found})")


def _fsm_state_findings(
    groups: dict[str, list[dict[str, Any]]],
    fsm_states: Optional[list[str]],
    add: Callable[[str], None],
) -> None:
    """One state port per declared state.

    The only port rule that is not a count budget: it compares the ports against
    the manifest's own ``states`` list, so it cannot live in
    :data:`_PORT_BUDGETS`.
    """
    if fsm_states and len(groups["state"]) != len(fsm_states):
        add(f"FSM has {len(fsm_states)} states but {len(groups['state'])} state ports (must match)")


#: port_type -> IN/OUT. Only ``in`` and ``external_in`` are IN; the control ports
#: (tool/policy/state/review) are all INVOKE sources.
_DIRECTION_BY_PORT_TYPE: dict[str, str] = {
    "external_in": "IN",
    "in": "IN",
    "out": "OUT",
    "tool": "OUT",
    "policy": "OUT",
    "state": "OUT",
    "external_out": "OUT",
    "review": "OUT",
}

# Manifest port sections -> (port_type, IN/OUT direction). A copy of the bundler's
# section tables, so ``validator`` stays independent of ``_bundle``.
_MANIFEST_PORT_SECTIONS: dict[str, tuple[str, str]] = {
    "input_ports": ("in", "IN"),
    "output_ports": ("out", "OUT"),
    "tool_ports": ("tool", "OUT"),
    "policy_ports": ("policy", "OUT"),
    "state_ports": ("state", "OUT"),
    "review_ports": ("review", "OUT"),
    "external_in_port": ("external_in", "IN"),
    "external_out_port": ("external_out", "OUT"),
}

#: Sections whose items may name their own ``port_type`` (the table value is the
#: default). The control sections above are fixed by the section they sit in.
_TYPED_SECTIONS = ("input_ports", "output_ports")

#: The keys a port item carries its name under, in the order the platform tries them.
_PORT_NAME_KEYS = ("port_name", "key", "name")


def _manifest_ports(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Every port a manifest declares, as rule-ready dicts.

    A component manifest declares ports in sections: ``input_ports`` /
    ``output_ports`` / ``tool_ports`` / ``policy_ports`` / ``state_ports`` /
    ``review_ports`` lists plus ``external_in_port`` / ``external_out_port``.
    The section fixes the port type and direction; the item carries no
    ``direction`` field.

    Returns dicts carrying ``key``, ``port_type`` and ``direction``, the three
    fields :func:`validate_port_rules` groups on.
    """
    return _sectioned_manifest_ports(parsed)


def _manifest_port_entry(item: dict[str, Any], port_type: str, direction: str) -> dict[str, Any]:
    """One rule-ready port dict; the name is the first non-blank name key."""
    key = ""
    for candidate in _PORT_NAME_KEYS:
        value = item.get(candidate)
        if isinstance(value, str) and value.strip():
            key = value.strip()
            break
    return {"key": key, "port_type": port_type, "direction": direction}


def _manifest_section_items(parsed: dict[str, Any], section: str) -> list[Any]:
    """A section's items under either spelling; ``[]`` if absent or not a list."""
    # The platform reads the singular and plural spelling of each section
    # interchangeably.
    alias = section[:-1] if section.endswith("s") else section + "s"
    block = parsed.get(section)
    if block is None:
        block = parsed.get(alias)
    if block is None:
        return []
    items = [block] if isinstance(block, dict) else block
    return items if isinstance(items, list) else []


def _sectioned_manifest_ports(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Ports from the canonical sectioned form, in section-table order."""
    ports: list[dict[str, Any]] = []
    for section, (port_type, direction) in _MANIFEST_PORT_SECTIONS.items():
        for item in _manifest_section_items(parsed, section):
            if not isinstance(item, dict):
                continue
            item_type = port_type
            if section in _TYPED_SECTIONS:
                item_type = str(item.get("port_type") or port_type)
            ports.append(
                _manifest_port_entry(
                    item, item_type, _DIRECTION_BY_PORT_TYPE.get(item_type, direction)
                )
            )
    return ports
