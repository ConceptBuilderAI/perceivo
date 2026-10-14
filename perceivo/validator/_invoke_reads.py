"""``VAL-CONTRACT-RESP``: fields read from a ``flow.invoke`` result.

For every ``r = await flow.invoke("port", ...)`` the fields the script reads
from ``r["response_data"]`` must be declared in that port's ``response_data``,
as the platform checks at import. Reading an undeclared field means the invoked
component may never provide it. Declared-but-unread is fine.

The extractors are conservative: an unrecognised read is not reported, and a
result variable or ``response_data`` alias bound to invokes of different ports
is ambiguous and skipped. Check id, severity, title and message match the
platform's.
"""

from __future__ import annotations

import ast
from typing import Any, Callable

AddCheck = Callable[..., None]

_ALL_PORT_SECTIONS = (
    "input_ports",
    "output_ports",
    "tool_ports",
    "state_ports",
    "policy_ports",
    "review_ports",
    "external_in_port",
    "external_out_port",
)


def _is_invoke_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "invoke"
    )


def _invoke_port_name(call: ast.Call) -> str | None:
    """``port_name`` from ``invoke(port_name=...)`` or positional arg 0."""
    for kw in call.keywords:
        if (
            kw.arg == "port_name"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _unambiguous_assignments(
    tree: ast.AST, port_of: Callable[[ast.expr], str | None]
) -> dict[str, str]:
    """Map each ``name = <expr>`` whose expr names a port; drop names bound to two ports."""
    bound: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            continue
        port = port_of(node.value)
        if not port:
            continue
        name = node.targets[0].id
        if name in bound and bound[name] != port:
            ambiguous.add(name)
        bound[name] = port
    for name in ambiguous:
        bound.pop(name, None)
    return bound


def _invoked_port(val: ast.expr) -> str | None:
    if isinstance(val, ast.Await):
        val = val.value
    return _invoke_port_name(val) if _is_invoke_call(val) else None  # type: ignore[arg-type]


def _response_data_source_port(val: ast.expr, var_to_port: dict[str, str]) -> str | None:
    """Port if ``val`` is ``r["response_data"]`` or ``r.get("response_data", ...)``."""
    if (
        isinstance(val, ast.Subscript)
        and isinstance(val.value, ast.Name)
        and val.value.id in var_to_port
        and isinstance(val.slice, ast.Constant)
        and val.slice.value == "response_data"
    ):
        return var_to_port[val.value.id]
    if (
        isinstance(val, ast.Call)
        and isinstance(val.func, ast.Attribute)
        and val.func.attr == "get"
        and isinstance(val.func.value, ast.Name)
        and val.func.value.id in var_to_port
        and val.args
        and isinstance(val.args[0], ast.Constant)
        and val.args[0].value == "response_data"
    ):
        return var_to_port[val.func.value.id]
    return None


def _read_field(
    node: ast.AST, alias_to_port: dict[str, str], var_to_port: dict[str, str]
) -> tuple[str, str] | None:
    """``(port, field)`` for ``container["field"]`` / ``container.get("field")``."""
    if isinstance(node, ast.Subscript):
        container, key = node.value, node.slice
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        container, key = node.func.value, node.args[0]
    else:
        return None
    if isinstance(container, ast.Name) and container.id in alias_to_port:
        port: str | None = alias_to_port[container.id]
    else:
        port = _response_data_source_port(container, var_to_port)
    if port and isinstance(key, ast.Constant) and isinstance(key.value, str):
        return port, key.value
    return None


def invoke_result_reads(tree: ast.AST) -> dict[str, set[str]]:
    """Fields the script reads from each ``flow.invoke`` result's ``response_data``.

    Tracks ``r = await flow.invoke("port", ...)`` then ``r["response_data"]["f"]``,
    ``r["response_data"].get("f")`` and ``r.get("response_data", {}).get("f")``,
    also through an intermediate ``rd = r["response_data"]``.
    """
    var_to_port = _unambiguous_assignments(tree, _invoked_port)
    if not var_to_port:
        return {}
    alias_to_port = _unambiguous_assignments(
        tree, lambda val: _response_data_source_port(val, var_to_port)
    )
    reads: dict[str, set[str]] = {p: set() for p in var_to_port.values()}
    for node in ast.walk(tree):
        field = _read_field(node, alias_to_port, var_to_port)
        if field is not None:
            reads[field[0]].add(field[1])
    return reads


def _ports_by_name(parsed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ports: dict[str, dict[str, Any]] = {}
    for section in _ALL_PORT_SECTIONS:
        entry = parsed.get(section) or []
        if isinstance(entry, dict):
            entry = [entry]
        if not isinstance(entry, list):
            continue
        for port in entry:
            if isinstance(port, dict):
                name = port.get("port_name") or port.get("name") or ""
                if name:
                    ports[name] = port
    return ports


def check_invoke_response_data_reads(
    tree: ast.AST,
    parsed: dict[str, Any],
    field_names: Callable[[Any], set[str]],
    add: AddCheck,
) -> None:
    """Report each invoke result read of a field the invoked port does not declare."""
    if not parsed:
        return
    reads = invoke_result_reads(tree)
    if not reads:
        return
    ports = _ports_by_name(parsed)
    for port_name, fields_read in reads.items():
        port = ports.get(port_name)
        declared = field_names(port.get("response_data")) if port else set()
        undeclared = sorted(fields_read - declared)
        if undeclared:
            add(
                "VAL-CONTRACT-RESP",
                "error",
                "contract",
                "Script reads undeclared invoke response_data",
                f"flow.invoke('{port_name}') result reads response_data fields "
                f"not declared on the port: {undeclared}",
                suggestion="Add these fields to the port's response_data in the YAML "
                "manifest, or stop reading them from the invoke result.",
            )
