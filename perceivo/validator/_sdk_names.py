"""Resolve a component script's ``perceivo`` names against the installed SDK.

Every ``perceivo`` import and module attribute chain the script evaluates at load
is resolved, and each failure is reported as the exception the runtime raises.
Names the script guards against absence are not judged: a ``try`` body whose
handler catches the failure, a ``with suppress(...)`` body that suppresses it, an
``if TYPE_CHECKING:`` body, a branch tested with ``hasattr``, the operands after a
``hasattr`` call in an ``and``, the arguments of that call, and annotations under
``from __future__ import annotations``. Only SDK modules are imported; the
component's own code is never executed.
"""

from __future__ import annotations

import ast
import importlib
import re
import types
from typing import Optional

_ROOT = "perceivo"

_GUARD_EXCEPTIONS = frozenset(
    {"ImportError", "ModuleNotFoundError", "AttributeError", "Exception", "BaseException"}
)

_TRY_NODES = tuple({ast.Try, getattr(ast, "TryStar", ast.Try)})

# The trailing "(<absolute path>.py)" ImportError appends; it names the local install.
_TRAILING_PATH = re.compile(r"\s*\((?:[A-Za-z]:)?[\\/][^()]*\.py\)$")


def _is_sdk_module(name: str) -> bool:
    return name == _ROOT or name.startswith(_ROOT + ".")


def _error_text(exc: BaseException) -> str:
    """Return ``"<type>: <message>"`` without any file path the message carries."""
    message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)
    return f"{type(exc).__name__}: {_TRAILING_PATH.sub('', message)}"


def _import_sdk_module(name: str) -> tuple[Optional[types.ModuleType], Optional[str]]:
    """Import an SDK module.

    Returns:
        ``(module, None)`` on success, otherwise ``(None, exception text)``.
    """
    try:
        return importlib.import_module(name), None
    except Exception as exc:  # an SDK import failure is reported, never raised
        return None, _error_text(exc)


def _resolve_attr(obj: object, attr: str) -> tuple[object, Optional[str]]:
    """Look up ``attr`` on ``obj``, falling back to importing an SDK submodule.

    Returns:
        ``(value, None)`` on success, otherwise ``(None, exception text)``.
    """
    try:
        return getattr(obj, attr), None
    except Exception as exc:  # a lazy SDK attribute may raise ImportError too
        error = _error_text(exc)
    if isinstance(obj, types.ModuleType) and _is_sdk_module(obj.__name__):
        module, _ = _import_sdk_module(f"{obj.__name__}.{attr}")
        if module is not None:
            return module, None
    return None, error


def _names(node: Optional[ast.expr]) -> list[str]:
    """Return the trailing names of a ``Name``/``Attribute`` or a tuple of them."""
    if node is None:
        return []
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    names = []
    for item in items:
        if isinstance(item, ast.Name):
            names.append(item.id)
        elif isinstance(item, ast.Attribute):
            names.append(item.attr)
    return names


def _catches_absence(handler: ast.ExceptHandler) -> bool:
    return handler.type is None or bool(_GUARD_EXCEPTIONS.intersection(_names(handler.type)))


def _suppresses_absence(item: ast.withitem) -> bool:
    call = item.context_expr
    return (
        isinstance(call, ast.Call)
        and _names(call.func) == ["suppress"]
        and any(_GUARD_EXCEPTIONS.intersection(_names(arg)) for arg in call.args)
    )


def _guards_body(node: ast.AST) -> bool:
    """Return whether ``node`` is a ``try`` or ``with`` whose body may lack a name."""
    if isinstance(node, _TRY_NODES):
        return any(_catches_absence(h) for h in node.handlers)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return any(_suppresses_absence(item) for item in node.items)
    return False


def _tests_type_checking(test: ast.expr) -> bool:
    return _names(test) == ["TYPE_CHECKING"]


def _is_hasattr_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hasattr"
    )


def _tests_hasattr(test: ast.expr) -> bool:
    return any(_is_hasattr_call(node) for node in ast.walk(test))


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _annotations(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.arg):
        return [node.annotation] if node.annotation else []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [node.returns] if node.returns else []
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    return []


def _unjudged_nodes(tree: ast.Module) -> set[int]:
    """Return the ids of nodes that do not fail the load when a name is absent."""
    future_annotations = _has_future_annotations(tree)
    regions: list[ast.AST] = []
    for node in ast.walk(tree):
        if _guards_body(node):
            regions.extend(node.body)
        elif _is_hasattr_call(node):
            regions.extend(node.args)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            tested = [i for i, value in enumerate(node.values) if _tests_hasattr(value)]
            if tested:
                regions.extend(node.values[tested[0] + 1 :])
        elif isinstance(node, (ast.If, ast.IfExp)):
            body = node.body if isinstance(node.body, list) else [node.body]
            if _tests_type_checking(node.test):
                regions.extend(body)
            elif _tests_hasattr(node.test):
                orelse = node.orelse if isinstance(node.orelse, list) else [node.orelse]
                regions.extend(body + orelse)
        if future_annotations:
            regions.extend(_annotations(node))
    return {id(sub) for region in regions for sub in ast.walk(region)}


def _bind_imports(
    tree: ast.Module, unjudged: set[int]
) -> tuple[dict[str, types.ModuleType], list[tuple[int, str]]]:
    """Map each local name bound to an SDK module to that module.

    Returns:
        The alias map, and ``(line, exception text)`` for each judged import that
        fails.
    """
    aliases: dict[str, types.ModuleType] = {}
    failures: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        judged = id(node) not in unjudged
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_sdk_module(alias.name):
                    continue
                module, error = _import_sdk_module(alias.name)
                if error is not None:
                    if judged:
                        failures.append((node.lineno, error))
                    continue
                if alias.asname:
                    aliases[alias.asname] = module
                else:
                    aliases[_ROOT] = importlib.import_module(_ROOT)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or not _is_sdk_module(node.module):
                continue
            module, error = _import_sdk_module(node.module)
            if error is not None:
                if judged:
                    failures.append((node.lineno, error))
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                value, attr_error = _resolve_attr(module, alias.name)
                if attr_error is not None:
                    if judged:
                        failures.append(
                            (
                                node.lineno,
                                f"ImportError: cannot import name '{alias.name}' "
                                f"from '{node.module}'",
                            )
                        )
                elif isinstance(value, types.ModuleType):
                    aliases[alias.asname or alias.name] = value
    return aliases, failures


def _attribute_chain(node: ast.Attribute) -> Optional[tuple[str, list[str]]]:
    """Split ``a.b.c`` into ``("a", ["b", "c"])``.

    Returns:
        The root name and attribute names, or None when the chain does not start at
        a name.
    """
    attrs: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    attrs.reverse()
    return current.id, attrs


def unresolved_sdk_names(tree: ast.Module) -> list[tuple[int, str]]:
    """Find the SDK names a script evaluates at load that do not resolve.

    An attribute chain is followed while it names modules; an attribute of a class
    or function is not judged.

    Args:
        tree: The parsed component script.

    Returns:
        Sorted, unique ``(line, exception text)`` pairs.
    """
    unjudged = _unjudged_nodes(tree)
    aliases, findings = _bind_imports(tree, unjudged)
    if aliases:
        nested = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or id(node) in nested or id(node) in unjudged:
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            chain = _attribute_chain(node)
            if chain is None or chain[0] not in aliases:
                continue
            obj: object = aliases[chain[0]]
            for attr in chain[1]:
                obj, error = _resolve_attr(obj, attr)
                if error is not None:
                    findings.append((node.lineno, error))
                    break
                if not isinstance(obj, types.ModuleType):
                    break
    return sorted(set(findings))
