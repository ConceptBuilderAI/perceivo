"""Judge an orchestration component that supplied no manifest at all.

A manifest-less script that uses any contract surface (``request_data`` fields,
``response_data``, or input and output artifacts) is reported as
``VAL-UNVERIFIABLE-MANIFEST``, a blocking error. A script that uses nothing
needs no manifest and stays valid.
"""

from __future__ import annotations

import ast
from typing import Callable

from ._script_facts import _returned_names, _returned_output_call

AddCheck = Callable[..., None]


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_request_data_ref(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "request_data":
        return True
    return isinstance(node, ast.Name) and node.id in aliases


def _request_data_alias_targets(node: ast.AST) -> list[str]:
    """Names an ``x = <...>.request_data`` (or ``... or ...``) assignment binds."""
    if not isinstance(node, ast.Assign):
        return []
    value = node.value
    if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or) and value.values:
        value = value.values[0]
    if isinstance(value, ast.Attribute) and value.attr == "request_data":
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    return []


def _is_request_data_field_read(node: ast.AST, aliases: set[str]) -> bool:
    """``request_data.get("k")`` or ``request_data["k"]`` on a known reference."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and _is_request_data_ref(node.func.value, aliases)
        and _const_str(node.args[0]) is not None
    ):
        return True
    return (
        isinstance(node, ast.Subscript)
        and _is_request_data_ref(node.value, aliases)
        and _const_str(node.slice) is not None
    )


def reads_request_data_fields(tree: ast.AST) -> bool:
    """Return True if the script reads any ``request_data`` field.

    Alias discovery is interleaved with access detection in one walk, as the
    platform does it, so a read the walk visits before its alias is bound is missed
    in both.
    """
    aliases: set[str] = set()
    found = False
    for node in ast.walk(tree):
        aliases.update(_request_data_alias_targets(node))
        if _is_request_data_field_read(node, aliases):
            found = True
    return found


def _returned_output_calls(tree: ast.AST) -> list[ast.Call]:
    returned = _returned_names(tree)
    calls = []
    for node in ast.walk(tree):
        call = _returned_output_call(node, returned)
        if call is not None:
            calls.append(call)
    return calls


def returns_response_data(tree: ast.AST) -> bool:
    """Return True if a returned Output's ``response_data`` shows or hides any key.

    Only a returned Output's ``response_data=`` counts. An empty dict literal is
    the one form that yields no key and stays resolvable; any other form either
    shows a key or is not resolvable.
    """
    for call in _returned_output_calls(tree):
        for kw in call.keywords:
            if kw.arg != "response_data":
                continue
            if not isinstance(kw.value, ast.Dict) or kw.value.keys:
                return True
    return False


def reads_input_artifact(tree: ast.AST) -> bool:
    """Return True if the script loads ``input.artifact`` or ``input.artifacts``."""
    return any(
        isinstance(node, ast.Attribute)
        and node.attr in ("artifact", "artifacts")
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "input"
        for node in ast.walk(tree)
    )


def emits_artifact(tree: ast.AST) -> bool:
    """Return True if a returned Output is given a non-None artifact."""
    return any(
        kw.arg in ("artifact", "artifacts")
        and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
        for call in _returned_output_calls(tree)
        for kw in call.keywords
    )


def report_absent_manifest(tree: ast.AST, component_type: str, add: AddCheck) -> None:
    """Report ``VAL-UNVERIFIABLE-MANIFEST`` when a manifest-less script uses a contract."""
    uses: list[str] = []
    if reads_request_data_fields(tree):
        uses.append("reads input.request_data fields")
    if returns_response_data(tree):
        uses.append("returns Output response_data")
    if reads_input_artifact(tree):
        uses.append("reads input.artifact")
    if emits_artifact(tree):
        uses.append("returns an artifact")
    if not uses:
        return
    reason = (
        "with no manifest there are no port declarations to check the script against, "
        "so every script<->manifest contract check is skipped. A script that uses "
        "nothing needs no manifest; this one does not qualify"
    )
    add(
        "VAL-UNVERIFIABLE-MANIFEST",
        "error",
        "unverifiable",
        "No manifest to validate the script against",
        f"This {component_type} component supplied no YAML manifest, but its script "
        f"{', '.join(uses)}. Could not verify: {reason}",
        suggestion="Add a YAML manifest declaring the port contracts this script actually "
        "uses (request_data / response_data fields and artifact_type_input/output).",
    )
