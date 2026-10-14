"""The port rule the platform applies first when it creates a component.

Creating a component, as import does for every bundled one, runs one rule per
component type and refuses with HTTP 400 at its first failure. It counts
``port_type`` exactly as written, an absent one as ``in``, so a type spelled in
another case is a port of no budgeted type: it fails only a rule that counts
every port, or none. :func:`create_refusal` returns that first refusal with the
platform's message. A type the platform does not know is refused before any
port is counted; a type it knows but sets no rule for is never refused here.

A component this rule accepts is then judged by the platform's case-folding
budgets (:mod:`._port_rules`). Import passes neither FSM states nor policy states
to this rule, so its FSM state-count and POLICY allowed-states checks never run
there and are not reproduced.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Callable, NamedTuple, Optional

from ._component_tables import ALL_COMPONENT_TYPES, unknown_component_type_message


class Refusal(NamedTuple):
    """The platform's refusal: its message, the port type it counted, and the count.

    ``port_type`` is None for a refusal that counts no single type.
    """

    message: str
    port_type: Optional[str]
    found: int


_Counts = dict[Any, int]
_Rule = Callable[[str, list[dict[str, Any]], _Counts], Optional[Refusal]]


def _exactly_one(
    port_type: str, category: str, ports: list[dict[str, Any]], counts: _Counts
) -> Optional[Refusal]:
    found = counts.get(port_type, 0)
    if found == 1:
        return None
    label = port_type.upper()
    return Refusal(f"{category} must have exactly 1 {label} port (found {found})", port_type, found)


def _at_most(
    high: int, port_type: str, category: str, ports: list[dict[str, Any]], counts: _Counts
) -> Optional[Refusal]:
    found = counts.get(port_type, 0)
    if found <= high:
        return None
    noun = "port" if high == 1 else "ports"
    message = f"{category} can have at most {high} {port_type.upper()} {noun} (found {found})"
    return Refusal(message, port_type, found)


def _none(
    port_type: str,
    category: str,
    ports: list[dict[str, Any]],
    counts: _Counts,
    message: Optional[str] = None,
) -> Optional[Refusal]:
    found = counts.get(port_type, 0)
    if found == 0:
        return None
    return Refusal(message or f"{category} cannot have {port_type.upper()} ports", port_type, found)


def _total(
    pair: str, category: str, ports: list[dict[str, Any]], counts: _Counts
) -> Optional[Refusal]:
    if len(ports) == 2:
        return None
    return Refusal(
        f"{category} must have exactly 2 ports ({pair}), found {len(ports)}", None, len(ports)
    )


def _no_ports(category: str, ports: list[dict[str, Any]], counts: _Counts) -> Optional[Refusal]:
    """Any port at all, whatever its type or direction."""
    if not ports:
        return None
    return Refusal(f"{category} components cannot define orchestration ports", None, len(ports))


def _review_out_message(found: int) -> str:
    return (
        f"REVIEW must have NO OUT port (found {found}). "
        "Its IN port is bidirectional -- the survey response returns "
        "through the same port. A declared OUT port cannot be connected "
        "to anything, because REVIEW calls no other component."
    )


def _review_out_direction(
    category: str, ports: list[dict[str, Any]], counts: _Counts
) -> Optional[Refusal]:
    """Any port whose direction is not IN, whatever its type; an absent one reads as IN."""
    found = sum(1 for p in ports if str(p.get("direction") or "IN").upper() != "IN")
    return Refusal(_review_out_message(found), None, found) if found else None


def _review_out_type(
    category: str, ports: list[dict[str, Any]], counts: _Counts
) -> Optional[Refusal]:
    found = counts.get("out", 0)
    return Refusal(_review_out_message(found), "out", found) if found else None


#: Each component type's rule, in the platform's order. A START or END never
#: reaches the platform's "cannot have" checks: once both counts are 1 and the
#: total is 2, no port of another type is left, so they are not listed.
_RULES: dict[str, tuple[_Rule, ...]] = {
    "AGENT": (
        partial(_exactly_one, "in"),
        partial(_at_most, 1, "out"),
        partial(_at_most, 50, "tool"),
        partial(_none, "state"),
        partial(_none, "policy"),
    ),
    "FSM": (
        partial(_exactly_one, "in"),
        partial(_exactly_one, "policy"),
        partial(_at_most, 50, "state"),
        partial(_at_most, 5, "out"),
        partial(_none, "tool"),
    ),
    "TOOL": (
        partial(_exactly_one, "in"),
        partial(_at_most, 50, "tool"),
        partial(_none, "out", message="TOOL cannot have OUT ports (tools don't return artifacts)"),
        partial(_none, "state"),
        partial(_none, "policy"),
    ),
    "POLICY": (
        partial(_exactly_one, "in"),
        partial(_at_most, 10, "tool"),
        partial(_none, "out"),
        partial(_none, "state"),
        partial(_none, "policy", message="POLICY cannot have nested POLICY ports"),
    ),
    "START": (
        partial(_exactly_one, "external_in"),
        partial(_exactly_one, "out"),
        partial(_total, "EXTERNAL_IN and OUT"),
    ),
    "END": (
        partial(_exactly_one, "in"),
        partial(_exactly_one, "external_out"),
        partial(_total, "IN and EXTERNAL_OUT"),
    ),
    "REVIEW": (
        _review_out_direction,
        partial(_exactly_one, "in"),
        _review_out_type,
        partial(_none, "state"),
        partial(_none, "tool"),
        partial(_none, "policy"),
    ),
    # A capability-level extension is not an orchestration node, so it exposes no port.
    "SCORING": (_no_ports,),
    "EVALUATOR": (_no_ports,),
}

#: The types the platform creates without a create-time port rule. It matches these in any
#: case, because an intelligence component's category is stored lower case.
_UNRULED = frozenset(ALL_COMPONENT_TYPES - set(_RULES))


def _unknown(component_type: str) -> Refusal:
    return Refusal(unknown_component_type_message(component_type), None, 0)


def known_type(component_type: str) -> bool:
    """Whether the platform creates *component_type*, matched as the platform matches it."""
    return component_type in _RULES or component_type.upper() in _UNRULED


def create_refusal(component_type: str, ports: list[dict[str, Any]]) -> Optional[Refusal]:
    """The platform's first refusal of *ports* for *component_type*, or None.

    *component_type* is matched as written against the types with a rule, and
    in any case against those without one, as the platform matches them.
    """
    rules = _RULES.get(component_type)
    if rules is None:
        return None if component_type.upper() in _UNRULED else _unknown(component_type)
    counts: _Counts = {}
    for port in ports:
        port_type = port.get("port_type", "in")
        counts[port_type] = counts.get(port_type, 0) + 1
    for rule in rules:
        refusal = rule(component_type, ports, counts)
        if refusal is not None:
            return refusal
    return None
