"""Top-level names a script binds to the ``perceivo.primitives`` module.

The ``response_data`` key resolution behind ``VAL-CONTRACT-RESP`` reads
``p.AgentOutput(...)`` as ``perceivo.primitives.AgentOutput(...)`` when ``p`` is
bound to that module, as the platform does at import.
"""

from __future__ import annotations

import ast

_ROOT = "perceivo"


def _bound_names(stmt: ast.stmt) -> list[str]:
    """Names a single top-level statement binds (imports, defs, assignments)."""
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".", 1)[0] for a in stmt.names]
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        targets = [stmt.target]
    names: list[str] = []
    for target in targets:
        names.extend(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
    return names


def primitives_aliases(tree: ast.AST) -> frozenset[str]:
    """Top-level names bound to the ``perceivo.primitives`` module itself.

    ``import perceivo.primitives as p`` and ``from perceivo import primitives as
    Q``. A later top-level statement that rebinds the name unbinds it.
    """
    names: set[str] = set()
    for stmt in getattr(tree, "body", []):
        for name in _bound_names(stmt):
            names.discard(name)
        if isinstance(stmt, ast.Import):
            names.update(
                a.asname for a in stmt.names if a.asname and a.name == f"{_ROOT}.primitives"
            )
        elif isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module == _ROOT:
            names.update(a.asname or a.name for a in stmt.names if a.name == "primitives")
    return frozenset(names)
