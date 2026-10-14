"""Recursive validation of nested orchestration projects.

An ORCHESTRATION node runs another project (``project:`` in the node, resolved
against the parent's directory as the runner resolves it). Each nested project
is validated in full, and its findings are carried into the parent's report
under the nested node's path, so a broken child fails its parent.
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Optional

from .._report import ValidationCheck, ValidationReport
from ._loader import _read_orchestration

# Resolved project directories on the current validation path, outermost first, so
# a project that nests itself cannot recurse without end.
_STACK: ContextVar[tuple[str, ...]] = ContextVar("perceivo_validator_nested_stack", default=())


def _node_label(node: dict[str, Any]) -> str:
    return str(node.get("name") or node.get("id") or "orchestration")


def nested_prefix(label: str) -> str:
    """What a finding inside the child project of the node named *label* starts with."""
    return f"In nested orchestration '{label}': "


def _nested_error(check_id: str, label: str, title: str, message: str) -> ValidationCheck:
    return ValidationCheck(
        id=f"{label}/{check_id}",
        severity="error",
        category="graph",
        title=title,
        message=nested_prefix(label) + message,
    )


def nested_project_checks(
    project_dir: str,
    nodes: list[dict[str, Any]],
    node_type: Callable[[str], str],
    validate_child: Callable[[str], ValidationReport],
) -> list[ValidationCheck]:
    """Validate every nested project the ORCHESTRATION nodes of *nodes* name.

    Each child finding keeps its severity and gains the nested node's name as a
    prefix on its id (``child/ORCH-...``) and message, so a failure three levels
    down reads ``outer/inner/ORCH-...``. A node that names a project directory
    which does not exist is an error: the runner would refuse it.
    """
    base = Path(project_dir).resolve()
    stack = _STACK.get() or (str(base),)
    checks: list[ValidationCheck] = []
    for node in nodes:
        if not isinstance(node, dict) or node_type(str(node.get("id", ""))) != "ORCHESTRATION":
            continue
        declared = node.get("project")
        if not declared:
            continue
        label = _node_label(node)
        child_dir = (base / str(declared)).resolve()
        if not child_dir.is_dir():
            checks.append(
                _nested_error(
                    "ORCH-NESTED-001",
                    label,
                    "Nested project not found",
                    f"project '{declared}' does not resolve to a directory under {base}.",
                )
            )
            continue
        if str(child_dir) in stack:
            checks.append(
                _nested_error(
                    "ORCH-NESTED-002",
                    label,
                    "Nested project cycle",
                    f"project '{declared}' is already being validated on this path "
                    f"({' -> '.join(Path(p).name for p in stack)}).",
                )
            )
            continue
        token = _STACK.set((*stack, str(child_dir)))
        try:
            child_report = validate_child(str(child_dir))
        finally:
            _STACK.reset(token)
        for check in child_report.checks:
            check.id = f"{label}/{check.id}"
            check.message = nested_prefix(label) + check.message
            checks.append(check)
    return checks


def nested_cycle(project_dir: str) -> Optional[str]:
    """The cycle validate reports for *project_dir*'s nested projects, if they nest one.

    Only the nesting is walked, so a run can refuse the cycle before it recurses
    into it, in validate's words.
    """

    def walk(directory: str) -> ValidationReport:
        orch = _read_orchestration(Path(directory) / "orchestration.yaml")
        nodes = orch.get("nodes") if isinstance(orch, dict) else None
        nodes = [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []
        types = {str(n.get("id", "")): str(n.get("component_type") or "") for n in nodes}
        checks = nested_project_checks(
            directory, nodes, lambda node_id: types.get(node_id, "").upper().strip(), walk
        )
        return ValidationReport(valid=not checks, checks=checks)

    for check in walk(project_dir).checks:
        if check.id.endswith("/ORCH-NESTED-002"):
            return check.message
    return None
