"""Port rules applied to the components as ``publish`` will bundle them.

The file checks judge each component manifest on its own, but ``publish`` does
not send the manifest's ports alone: the bundler merges the ports each
``orchestration.yaml`` node declares into its component's ports
(``perceivo._bundle.component.node_declared_ports``). A node port that the
manifest names differently is added rather than merged, so a project whose every
file passes can still yield a START with two EXTERNAL_IN ports. The bundler
refuses that with the platform's message, and this module reports the refusal
as an error.

This module builds the same bundle and runs the platform's port budgets
(``_port_rules.validate_port_rules``) over each bundled component. A finding
whose message the report already carries (the component checks judge the
manifest and the node's own port list) is not repeated, so one defect is not
counted twice. A skipped finding cannot turn a failing report into a passing
one: its message is already an error in that report.

A component is judged only when its bundled category is the ``component_type``
of every node that uses it. A manifest with no ``extension_type`` bundles as an
AGENT whatever its node says; that is a different defect from the one this
check exists for, and budgeting a START node's ports as an AGENT's would report
it under the wrong name.
"""

from __future__ import annotations

from typing import Any, Callable

from .._component_tables import unknown_component_type_message
from .._create_ports import known_type
from .._port_rules import validate_port_rules
from .._report import ValidationCheck
from ._bundle_connections import unconnected_port_checks

#: Why a bundled component can carry a port its manifest lacks. A type the
#: platform does not create is refused before any port is read, so it takes none.
_MERGE_NOTE = (
    "The ports its node declares in orchestration.yaml are merged into the manifest's by "
    "name and direction; a node port named differently from the manifest's is added as "
    "another port."
)


def _bundled_message(name: Any, category: str, finding: str) -> str:
    head = f"Component '{name}' ({category}) as publish bundles it: {finding.rstrip('.')}."
    return f"{head} {_MERGE_NOTE}" if known_type(category) else head


def _judged_refs(bundle: dict[str, Any], node_type: Callable[[str], str]) -> set[str]:
    """Component refs whose category agrees with every node that references them."""
    components = bundle.get("components") or {}
    agreeing = set(components)
    for node in bundle.get("nodes") or []:
        ref = str(node.get("component_ref") or "")
        component = components.get(ref)
        if component is None:
            continue
        if str(component.get("category") or "").upper() != node_type(str(node.get("local_id"))):
            agreeing.discard(ref)
    return agreeing


def _bundling_failure(exc: Exception, conflict_note: str) -> ValidationCheck:
    """The finding for a project that could not be bundled.

    A mainflow node whose two ports declare conflicting response contracts, or a
    START or END without exactly its two ports, is an error: the bundle is
    refused, so publish would refuse it too. Any other bundling failure is a
    warning that the bundled ports went unchecked.
    """
    from perceivo._bundle._port_consistency import TerminalPortShapeError

    if isinstance(exc, TerminalPortShapeError):
        return ValidationCheck(
            id="ORCH-BUNDLE-PORTS-001",
            severity="error",
            category="interface",
            title="Bundled component violates a port rule",
            message=str(exc),
        )
    if conflict_note in getattr(exc, "__notes__", ()):
        return ValidationCheck(
            id="ORCH-BUNDLE-CONTRACT-001",
            severity="error",
            category="contract",
            title="Conflicting response contracts on a mainflow node",
            message=str(exc),
        )
    return ValidationCheck(
        id="ORCH-BUNDLE-UNVERIFIED-001",
        severity="warning",
        category="interface",
        title="Bundled ports not verifiable",
        message=(
            "The project could not be bundled as publish would send it, so the "
            f"bundled component ports were not checked: {exc}"
        ),
    )


def bundled_port_checks(
    project_dir: str,
    node_type: Callable[[str], str],
    reported: set[str],
    *,
    file_refused: bool = False,
) -> list[ValidationCheck]:
    """Port-rule findings on the bundle ``publish`` would send for *project_dir*.

    *node_type* maps a node id to its upper-cased ``component_type``. *reported*
    is the set of messages already in the report; a bundled finding with one of
    those messages is skipped. *file_refused* says a node's component record,
    script or nested ``orchestration.yaml`` is already reported as unreadable; that
    error replaces the warning that the bundled ports went unchecked.
    """
    from perceivo._bundle._mainflow_ports import CONTRACT_CONFLICT
    from perceivo._bundle.orchestration import orchestration_to_bundle

    try:
        bundle = orchestration_to_bundle(project_dir)
    except Exception as exc:  # any bundling failure is reported, never raised
        if getattr(exc, "finding", None) in reported:
            return []
        failure = _bundling_failure(exc, CONTRACT_CONFLICT)
        if file_refused and failure.severity == "warning":
            return []
        return [failure]

    checks: list[ValidationCheck] = []
    judged = _judged_refs(bundle, node_type)
    for ref, component in (bundle.get("components") or {}).items():
        category = str(component.get("category") or "")
        # Judged whatever its nodes say: the platform refuses to create the
        # component before any node is read, and with --no-scripts nothing else
        # reads the category.
        if not known_type(category):
            message = unknown_component_type_message(category)
            if message not in reported:
                checks.append(
                    ValidationCheck(
                        id=f"ORCH-BUNDLE-PORTS-{len(checks) + 1:03d}",
                        severity="error",
                        category="interface",
                        title="Bundled component has an unknown type",
                        message=f"Component '{component.get('name')}' as publish bundles it: {message}",
                    )
                )
            continue
        if ref not in judged:
            continue
        for finding in validate_port_rules(category, component.get("ports") or []):
            if finding.message in reported:
                continue
            checks.append(
                ValidationCheck(
                    id=f"ORCH-BUNDLE-PORTS-{len(checks) + 1:03d}",
                    severity="error",
                    category="interface",
                    title="Bundled component violates a port rule",
                    message=_bundled_message(component.get("name"), category, finding.message),
                )
            )
    return checks + unconnected_port_checks(bundle)
