"""Define ComponentInfo, the valid component types and entry class detection."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

_VALID_TYPES = {"AGENT", "TOOL", "START", "END", "FSM", "POLICY", "ADAPTER"}


@dataclass
class ComponentInfo:
    """Details about a component loaded from script + manifest files."""

    name: str
    component_type: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    script_path: str = ""
    manifest_path: str = ""
    ports: list[dict[str, Any]] = field(default_factory=list)
    input_parameters: list[dict[str, Any]] = field(default_factory=list)
    required_secrets: list[dict[str, Any]] = field(default_factory=list)
    fsm_states: list[str] = field(default_factory=list)
    entry_class: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry_class_from_assign(node: ast.Assign) -> Optional[str]:
    """Return the ENTRY_CLASS string literal from an assignment, if present."""
    if not isinstance(node.value, (ast.Constant, ast.Str)):
        return None
    val = getattr(node.value, "value", getattr(node.value, "s", None))
    if not isinstance(val, str):
        return None
    for target in node.targets:
        if isinstance(target, ast.Name) and target.id == "ENTRY_CLASS":
            return val
    return None


def _entry_class_from_decorator(node: ast.AsyncFunctionDef) -> Optional[str]:
    """Return the function name if it carries a ``perceivo`` decorator."""
    for dec in node.decorator_list:
        if "perceivo" in ast.dump(dec):
            return node.name
    return None


def _detect_entry_class(code: str) -> Optional[str]:
    """Detect ENTRY_CLASS from source code via AST analysis."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            found = _entry_class_from_assign(node)
            if found is not None:
                return found

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            found = _entry_class_from_decorator(node)
            if found is not None:
                return found

    return None
