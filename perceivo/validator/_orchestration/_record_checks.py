"""Refuse, per node, a component record that export would refuse.

``publish`` builds each node's component with the exporter, which overlays the
record beside the script (``<stem>.component.json``) onto the manifest. A record
it cannot read, or whose fields fail the bundle schema, refuses the whole
bundle; the bundled-ports check can then only warn that the ports went
unchecked, and the project would validate while export refuses it.
"""

from __future__ import annotations

from typing import Any

from .._component import _recorded_rows_error
from .._paths import NOT_RECORDED, recorded_ports
from .._report import ValidationCheck


def component_record_checks(project_dir: str, loaded: dict[str, Any]) -> list[ValidationCheck]:
    """An error for each node whose component record export would refuse.

    Export reads the record beside each node's script with the reader
    :func:`recorded_ports` shares, and a refusal stops the whole bundle; the
    bundled-ports check can then only warn that it went unchecked. Judged with
    or without script checks, since export refuses either way.
    """
    from perceivo._internal.paths import resolve_within_project

    # The loader builds one component per node that names a script, in node order.
    scripted = [n for n in loaded.get("nodes") or [] if isinstance(n, dict) and n.get("script")]
    checks: list[ValidationCheck] = []
    for node, comp in zip(scripted, loaded.get("components", []), strict=True):
        # Export bundles an ORCHESTRATION node's project, never its script.
        if str(node.get("component_type") or "").upper() == "ORCHESTRATION":
            continue
        script = resolve_within_project(
            project_dir, node["script"], field="script", node_id=node.get("id") or node.get("name")
        )
        if not script.is_file():
            continue
        rows = recorded_ports(script, comp.get("manifest_yaml") or None)
        error = None if rows is NOT_RECORDED else _recorded_rows_error(rows)
        if error:
            checks.append(_record_error(str(comp.get("name", "unknown")), "VAL-RECORD-001", error))
    return checks


def _record_error(name: str, check_id: str, error: str) -> ValidationCheck:
    # ``{name}/{check_id}`` from parameters is the id shape the support catalog resolves.
    return ValidationCheck(
        id=f"{name}/{check_id}",
        severity="error",
        category="manifest",
        title="Component record cannot be exported",
        message=f"The component record cannot be exported: {error}",
        suggestion="Re-import the component, or edit its manifest so the manifest owns the ports.",
    )
