"""Which component type a script is validated or run as.

``perceivo validate component``, ``perceivo run component``, the path API
``perceivo.validator.validate_component`` and the manifest's MAN-TYPE check all
resolve the type here, in this order, and never default it:

1. an explicit type (``--type`` or ``component_type``);
2. the manifest's ``extension_type``, else its ``category``;
3. the script itself, read statically: a component decorator
   (``@perceivo.review``, ``@perceivo.primitives.tool``, ``@adapter`` imported
   from ``perceivo.primitives``) or a class deriving from a component base
   (``perceivo.primitives.Policy``, ``perceivo.intelligence.FeatureComponent``).
   When the script names ``ENTRY_CLASS``, only that class is read.

A script that names no component type, or names two different ones, is refused
with :data:`CANNOT_INFER`. The script is parsed, never imported or executed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Optional

import yaml

#: Error message shown when the component type cannot be inferred.
CANNOT_INFER = "cannot infer component type; pass --type or a manifest"

#: Component decorator name -> component type.
_DECORATOR_TYPES: dict[str, str] = {
    "agent": "AGENT",
    "tool": "TOOL",
    "start": "START",
    "end": "END",
    "fsm": "FSM",
    "policy": "POLICY",
    "adapter": "ADAPTER",
    "review": "REVIEW",
}

#: Component base class name -> component type.
_BASE_TYPES: dict[str, str] = {
    "Agent": "AGENT",
    "Tool": "TOOL",
    "Start": "START",
    "End": "END",
    "FSM": "FSM",
    "Policy": "POLICY",
    "Adapter": "ADAPTER",
    "Review": "REVIEW",
    "FeatureComponent": "FEATURE",
    "TrainingComponent": "TRAINING",
    "InferenceComponent": "INFERENCE",
    "EvaluationComponent": "EVALUATION",
    "ScoringComponent": "SCORING",
    "EvaluatorComponent": "EVALUATOR",
}


class ComponentTypeError(ValueError):
    """The component type could not be decided; the message says why."""


def _is_perceivo(module: str) -> bool:
    return module.split(".")[0] == "perceivo"


def _module_aliases(node: ast.Import) -> set[str]:
    """The local names ``node`` binds to a ``perceivo`` module."""
    return {
        alias.asname or alias.name.split(".")[0] for alias in node.names if _is_perceivo(alias.name)
    }


def _perceivo_aliases(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """Names bound to a ``perceivo`` module, and names imported from one.

    Returns ``(module_aliases, imported)``: ``import perceivo as p`` adds ``p``
    to the first; ``from perceivo.primitives import Policy as P`` maps ``P`` to
    ``Policy`` in the second.
    """
    modules: set[str] = set()
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(_module_aliases(node))
        elif isinstance(node, ast.ImportFrom) and _is_perceivo(node.module or ""):
            imported.update({alias.asname or alias.name: alias.name for alias in node.names})
    return modules, imported


def _leaf(expr: ast.expr, modules: set[str], imported: dict[str, str]) -> Optional[str]:
    """The SDK name *expr* refers to, or ``None`` when it is not an SDK name."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    if isinstance(expr, ast.Name):
        return imported.get(expr.id)
    if isinstance(expr, ast.Attribute):
        root = expr.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in modules:
            return expr.attr
    return None


def _entry_class(tree: ast.Module) -> Optional[str]:
    """The literal value of a module-level ``ENTRY_CLASS = "..."``, if any."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "ENTRY_CLASS" in names and isinstance(node.value.value, str):
                return node.value.value
    return None


def _kinds(
    exprs: list[ast.expr], table: dict[str, str], modules: set[str], imported: dict[str, str]
) -> set[str]:
    """The component types that ``exprs`` name through ``table``."""
    kinds = (table.get(_leaf(expr, modules, imported) or "") for expr in exprs)
    return {kind for kind in kinds if kind}


def _declared_types(
    node: ast.stmt, entry: Optional[str], modules: set[str], imported: dict[str, str]
) -> set[str]:
    """Types a top-level statement declares: by decorator, or by an entry class's base."""
    found: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        found |= _kinds(node.decorator_list, _DECORATOR_TYPES, modules, imported)
    if isinstance(node, ast.ClassDef) and (entry is None or node.name == entry):
        found |= _kinds(node.bases, _BASE_TYPES, modules, imported)
    return found


def infer_from_script(source: str) -> str:
    """The component type *source* declares, read without executing it.

    Raises:
        ComponentTypeError: The script declares no component type, declares two
            different ones, or does not parse.
    """
    try:
        tree = ast.parse(source.removeprefix("\ufeff"))
    except SyntaxError as exc:
        raise ComponentTypeError(f"{CANNOT_INFER} (the script does not parse: {exc.msg})") from exc
    modules, imported = _perceivo_aliases(tree)
    entry = _entry_class(tree)
    found: set[str] = set()
    for node in tree.body:
        found |= _declared_types(node, entry, modules, imported)
    if not found:
        raise ComponentTypeError(f"{CANNOT_INFER} (the script declares no component type)")
    if len(found) > 1:
        raise ComponentTypeError(f"{CANNOT_INFER} (the script declares {', '.join(sorted(found))})")
    return found.pop()


def manifest_declared_type(manifest: dict[str, Any]) -> Optional[str]:
    """A parsed manifest's ``extension_type``, else its ``category``, upper-cased."""
    value = manifest.get("extension_type") or manifest.get("category")
    return str(value).upper().strip() if value else None


def _manifest_type(manifest_yaml: Optional[str]) -> Optional[str]:
    """The manifest's declared type, or ``None`` when it names none."""
    if not manifest_yaml:
        return None
    try:
        manifest = yaml.safe_load(manifest_yaml)
    except yaml.YAMLError:
        return None
    if not isinstance(manifest, dict):
        return None
    return manifest_declared_type(manifest)


def resolve_component_type(
    script_path: str, explicit: Optional[str], manifest_yaml: Optional[str]
) -> str:
    """``--type``, else the manifest's ``extension_type`` or ``category``, else the script's own.

    The script is read only when neither of the first two names a type, so an
    explicit type over a missing script still reaches the runner's own report.

    Raises:
        ComponentTypeError: Nothing names a type, the script names two, or the
            script cannot be read.
    """
    if explicit:
        return explicit.upper().strip()
    declared = _manifest_type(manifest_yaml)
    if declared:
        return declared
    try:
        source = Path(script_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ComponentTypeError(f"{CANNOT_INFER} (cannot read {script_path}: {exc})") from exc
    return infer_from_script(source)
