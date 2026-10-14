"""Shape a test's run input to what the project's START declares.

The local runner refuses a run whose input does not match the request data
the START declares. Tests that exercise shipped templates and
platform bundles pass a generic input; this keeps the declared keys of that
input and fills every other required field with a value of its declared type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from perceivo.runner._start_input import declared_fields
from perceivo.runner.compiler import compile_project

_TYPED: dict[str, Any] = {
    "integer": 1,
    "int": 1,
    "number": 1.0,
    "float": 1.0,
    "boolean": True,
    "bool": True,
    "object": {},
    "dict": {},
    "array": [],
    "list": [],
}


def fit_start_input(project: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    """``data`` restricted to the declared keys, plus a typed value for each missing one."""
    plan = compile_project(str(project))
    fields = declared_fields(plan.shells_by_id[plan.start_node_id])
    if not fields:
        return dict(data)
    fitted = {key: value for key, value in data.items() if key in fields}
    for name, spec in fields.items():
        if spec["required"] and name not in fitted:
            fitted[name] = _TYPED.get(str(spec.get("type", "")).lower(), "hi")
    return fitted
