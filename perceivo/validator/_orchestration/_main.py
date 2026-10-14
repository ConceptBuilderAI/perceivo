"""Top-level orchestration validation entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .._contracts import validate_artifact_constraints, validate_port_contract_match
from .._report import ValidationCheck, ValidationReport
from .._type_names import (
    invalid_declaration_schema_types,
    invalid_port_schema_types,
    invalid_property_type_message,
)
from ._bundle_ports import bundled_port_checks
from ._component_checks import component_checks
from ._constants import ALLOWED_CONNECTIONS, MAINFLOW_EDGE_KINDS
from ._edges import (
    Finding,
    edge_rule_findings,
    mainflow_continuation_findings,
    port_type_findings,
)
from ._loader import _load_from_directory
from ._mainflow_dataflow import (
    _downstream_contract_port,
    compute_available_response_fields,
    with_effective_contract,
)
from ._nested import nested_project_checks
from ._nested_ports import apply_nested_port_fields, unresolved_nested_nodes
from ._node_rules import (
    _validate_phantom_mainflow_targets,
    _validate_required_input_parameters,
    _validate_required_secrets,
    _validate_review_survey_spec,
)
from ._ports import (
    _validate_all_ports_connected,
    _validate_declared_slots_listed,
    _validate_fsm_requires_policy,
    _validate_fsm_state_ports_connected,
    _validate_single_edge_per_port,
)
from ._record_checks import component_record_checks
from ._review_answer_keys import validate_review_answer_keys
from ._structure import (
    _validate_end_connections,
    _validate_fsm_depth,
    _validate_mainflow_structure,
)
from ._unreadable import load_failure_check, refuses_bundle


def _append_graph_findings(checks: list[ValidationCheck], findings: Any) -> None:
    """Append ``(stem, title, message)`` findings as numbered graph errors, in place."""
    for stem, title, message in findings:
        checks.append(
            ValidationCheck(
                id=f"{stem}-{len(checks) + 1:03d}",
                severity="error",
                category="graph",
                title=title,
                message=message,
            )
        )


def _property_type_findings(
    nodes: list[dict[str, Any]], ports: list[dict[str, Any]]
) -> list[Finding]:
    """Return every port schema type and declaration ``_schema_type`` outside the vocabulary.

    The platform validates the same values on write and refuses a non-canonical
    spelling (``number``, ``int``, ``double``) with HTTP 400 rather than rewriting
    it. A declaration's widget ``type`` is not judged: ``number`` is a legitimate
    numeric editor there.
    """
    findings: list[Finding] = []
    for port in ports:
        if not isinstance(port, dict):
            continue
        for path, value in invalid_port_schema_types(port, f"port '{port.get('id', '')}'"):
            findings.append(
                (
                    "ORCH-PROPTYPE",
                    "Invalid property type",
                    invalid_property_type_message(value, path),
                )
            )
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for path, value in invalid_declaration_schema_types(
            node.get("config"), f"node '{node.get('id', '')}'"
        ):
            findings.append(
                (
                    "ORCH-PROPTYPE",
                    "Invalid property type",
                    invalid_property_type_message(value, path),
                )
            )
    return findings


def _load_project(
    project_dir: str, checks: list[ValidationCheck], validate_scripts: bool
) -> Optional[dict[str, Any]]:
    """Load a project directory, appending the load failure or component checks to ``checks``."""
    loaded = _load_from_directory(project_dir)
    if loaded is None:
        checks.append(load_failure_check(project_dir))
        return None
    checks.extend(component_record_checks(project_dir, loaded))
    checks.extend(component_checks(loaded, validate_scripts))
    return loaded


def _append_connection_check(
    checks: list[ValidationCheck], edge_kind: Any, source_type: str, target_type: str
) -> None:
    """Append the connection-matrix check for one edge, if it applies and fails."""
    # The connection matrix applies only to INVOKE edges: on the platform the mainflow
    # is node config, and the chain is validated structurally elsewhere.
    if str(edge_kind).upper() in MAINFLOW_EDGE_KINDS:
        return
    allowed = ALLOWED_CONNECTIONS.get(source_type, set())
    if target_type and target_type not in allowed:
        checks.append(
            ValidationCheck(
                id=f"ORCH-CONN-{len(checks) + 1:03d}",
                severity="error",
                category="graph",
                title="Invalid connection",
                message=f"{source_type} cannot connect to {target_type}.",
            )
        )


def _contract_source(
    source_port: dict[str, Any], edge_kind: Any, available_response_fields: dict[str, Any]
) -> dict[str, Any]:
    """The source port a contract check sees, with mainflow response fields applied."""
    if str(edge_kind).upper() not in MAINFLOW_EDGE_KINDS:
        return source_port
    source_node_id = str(source_port.get("node_id", ""))
    if source_node_id not in available_response_fields:
        return source_port
    return {
        **source_port,
        "response_data_schema": available_response_fields[source_node_id],
    }


def _unresolved_on_edge(
    source_port: dict[str, Any],
    target_port: dict[str, Any],
    unresolved_nodes: frozenset[str],
) -> list[str]:
    """Ids of this edge's nodes that nest an orchestration not resolved locally."""
    return [
        str(p.get("node_id"))
        for p in (source_port, target_port)
        if str(p.get("node_id", "")) in unresolved_nodes
    ]


def _append_artifact_checks(
    checks: list[ValidationCheck],
    source_port: dict[str, Any],
    target_port: dict[str, Any],
    port_map: dict[str, dict[str, Any]],
    edge_kind: Any,
    unresolved_nodes: frozenset[str],
) -> None:
    """Append the artifact-type checks for one edge, in place."""
    # A config-declared hop's source is a synthesized ``virtual`` port with no artifact
    # types, so check the source node's real downstream port instead.
    unresolved = _unresolved_on_edge(source_port, target_port, unresolved_nodes)
    if unresolved:
        # The platform generates this port from the nested child, which is not
        # available locally, so its artifact types are unknown rather than absent.
        checks.append(
            ValidationCheck(
                id=f"ORCH-ARTIFACT-UNVERIFIED-{len(checks) + 1:03d}",
                severity="warning",
                category="contract",
                title="Artifact types not verifiable",
                message=(
                    f"Node '{unresolved[0]}' nests an orchestration that is not resolved "
                    "locally (no 'project'), so the artifact types on this edge cannot be "
                    "checked. Import with its capability members to check them."
                ),
            )
        )
        return

    artifact_source = source_port
    if source_port.get("virtual"):
        artifact_source = (
            _downstream_contract_port(str(source_port.get("node_id", "")), list(port_map.values()))
            or source_port
        )
    artifact_errors = validate_artifact_constraints(
        artifact_source, target_port, edge_kind=str(edge_kind or "")
    )
    for err in artifact_errors:
        checks.append(
            ValidationCheck(
                id=f"ORCH-ARTIFACT-{len(checks) + 1:03d}",
                severity="error",
                category="contract",
                title="Artifact type mismatch",
                message=err,
            )
        )


def _append_contract_checks(
    checks: list[ValidationCheck],
    source_port: dict[str, Any],
    target_port: dict[str, Any],
    edge_kind: Any,
    available_response_fields: dict[str, Any],
    ports: list[dict[str, Any]],
    unresolved_sides: frozenset[str] = frozenset(),
) -> None:
    """Append the port-contract checks for one edge, in place.

    ``unresolved_sides`` names the ends (``"source"``, ``"target"``) whose node
    nests an orchestration not resolved locally. Those ends' fields are unknown,
    so field coverage is not compared; a malformed schema on a resolved end is
    still an error, because it is wrong whatever the nested child declares.
    """
    contract_source = with_effective_contract(
        _contract_source(source_port, edge_kind, available_response_fields), ports
    )
    contract_errors = validate_port_contract_match(
        contract_source,
        with_effective_contract(target_port, ports),
        edge_kind=edge_kind,
    )
    if unresolved_sides:
        resolved = {"source", "target"} - unresolved_sides
        contract_errors = [
            err
            for err in contract_errors
            if any(err.startswith(f"Invalid {side} ") for side in resolved)
        ]
    for err in contract_errors:
        checks.append(
            ValidationCheck(
                id=f"ORCH-CONTRACT-{len(checks) + 1:03d}",
                severity="error",
                category="contract",
                title="Port contract mismatch",
                message=err,
            )
        )


def _append_edge_checks(
    checks: list[ValidationCheck],
    edge: dict[str, Any],
    port_map: dict[str, dict[str, Any]],
    node_type: Any,
    available_response_fields: dict[str, Any],
    unresolved_nodes: frozenset[str] = frozenset(),
) -> None:
    """Append connection, contract, and artifact checks for a single edge.

    Appends to ``checks`` in place; check ids derive from the running
    ``len(checks)``.
    """
    source_port = port_map.get(edge.get("source_port_id", ""))
    target_port = port_map.get(edge.get("target_port_id", ""))
    if not source_port or not target_port:
        return

    edge_kind = edge.get("kind", edge.get("edge_kind", "MAINFLOW"))
    source_type = node_type(source_port.get("node_id", ""))
    target_type = node_type(target_port.get("node_id", ""))

    # Structural rules: direction, layered source and bidirectional port pairing.
    _append_graph_findings(checks, edge_rule_findings(edge, port_map, node_type))

    _append_connection_check(checks, edge_kind, source_type, target_type)

    unresolved_sides = frozenset(
        side
        for side, port in (("source", source_port), ("target", target_port))
        if str(port.get("node_id", "")) in unresolved_nodes
    )
    _append_contract_checks(
        checks,
        source_port,
        target_port,
        edge_kind,
        available_response_fields,
        list(port_map.values()),
        unresolved_sides,
    )
    unresolved = _unresolved_on_edge(source_port, target_port, unresolved_nodes)
    if unresolved:
        # The platform generates this port's contract from the nested child, which
        # is not available locally, so its fields are unknown rather than absent.
        checks.append(
            ValidationCheck(
                id=f"ORCH-CONTRACT-UNVERIFIED-{len(checks) + 1:03d}",
                severity="warning",
                category="contract",
                title="Port contract not verifiable",
                message=(
                    f"Node '{unresolved[0]}' nests an orchestration that is not resolved "
                    "locally (no 'project'), so the fields this edge exchanges with it "
                    "cannot be checked. Import with its capability members to check them."
                ),
            )
        )

    _append_artifact_checks(checks, source_port, target_port, port_map, edge_kind, unresolved_nodes)


def validate_orchestration(
    path: str | Path,
    *,
    validate_scripts: bool = True,
) -> ValidationReport:
    """Validate an orchestration project on disk.

    Args:
        path: The project directory holding ``orchestration.yaml`` and the
            component files.
        validate_scripts: Whether to also validate each component's script.

    Returns:
        A :class:`ValidationReport` with all findings.

    Example:
        ```python
        report = perceivo.validator.validate_orchestration("./my_pipeline")
        print(report.valid, [c.message for c in report.errors])
        ```
    """
    return _validate(str(path), validate_scripts=validate_scripts)


def validate_graph(
    *,
    nodes: Optional[list[dict[str, Any]]] = None,
    edges: Optional[list[dict[str, Any]]] = None,
    ports: Optional[list[dict[str, Any]]] = None,
    component_types: Optional[dict[str, str]] = None,
) -> ValidationReport:
    """Validate an orchestration graph held in memory (private).

    Args:
        nodes: List of node dicts (id, component_type, ...).
        edges: List of edge dicts (source_port_id, target_port_id, kind).
        ports: List of port dicts (id, node_id, direction, port_type, ...).
        component_types: Optional mapping of node_id to component type.
    """
    return _validate(None, nodes=nodes, edges=edges, ports=ports, component_types=component_types)


def _validate(
    project_dir: Optional[str],
    *,
    nodes: Optional[list[dict[str, Any]]] = None,
    edges: Optional[list[dict[str, Any]]] = None,
    ports: Optional[list[dict[str, Any]]] = None,
    component_types: Optional[dict[str, str]] = None,
    validate_scripts: bool = True,
) -> ValidationReport:
    """The one implementation behind both entry points."""
    checks: list[ValidationCheck] = []
    bundled_ports: Optional[dict[str, list[dict[str, Any]]]] = None

    if project_dir is not None:
        loaded = _load_project(project_dir, checks, validate_scripts)
        if loaded is None:
            return ValidationReport(valid=False, checks=checks)
        nodes = loaded["nodes"]
        edges = loaded["edges"]
        ports = loaded["ports"]
        component_types = loaded["component_types"]
        bundled_ports = loaded.get("bundled_ports")

    if not nodes or not edges or not ports:
        checks.append(
            ValidationCheck(
                id="ORCH-EMPTY-001",
                severity="error",
                category="graph",
                title="Empty orchestration",
                message="Orchestration has no nodes, edges, or ports.",
            )
        )
        return ValidationReport(valid=False, checks=checks)

    types_map = component_types or {}
    node_map = {n["id"]: n for n in nodes}
    port_map = {p["id"]: p for p in ports}

    def _node_type(node_id: str) -> str:
        t = types_map.get(node_id, "")
        if not t and node_id in node_map:
            t = node_map[node_id].get("component_type", "")
        return t.upper().strip()

    # A nested node's ports carry the request and response fields the platform
    # generates from the child's START and END (see _nested_ports).
    if project_dir is not None:
        apply_nested_port_fields(project_dir, nodes, ports, _node_type, _load_from_directory)

    _append_graph_findings(checks, _property_type_findings(nodes, ports))

    checks.extend(_validate_mainflow_structure(nodes, edges, ports, _node_type))
    checks.extend(_validate_end_connections(nodes, edges, port_map, _node_type))
    checks.extend(_validate_fsm_depth(nodes, edges, ports, _node_type))

    # Node-scoped rules: a port's port_type must be legal for its node's category, and
    # only a mainflow-capable component may continue the mainflow.
    node_findings = list(port_type_findings(ports, _node_type))
    node_findings += mainflow_continuation_findings(nodes, ports, edges, _node_type)
    _append_graph_findings(checks, node_findings)

    available_response_fields = compute_available_response_fields(nodes, ports, edges)
    unresolved_nodes = (
        unresolved_nested_nodes(nodes, _node_type) if project_dir is not None else frozenset()
    )
    for edge in edges:
        _append_edge_checks(
            checks, edge, port_map, _node_type, available_response_fields, unresolved_nodes
        )

    checks.extend(_validate_single_edge_per_port(ports, edges))
    checks.extend(_validate_all_ports_connected(ports, edges, _node_type, nodes))
    checks.extend(_validate_fsm_requires_policy(nodes, ports, edges, _node_type))
    checks.extend(_validate_fsm_state_ports_connected(nodes, ports, edges, _node_type))
    checks.extend(_validate_declared_slots_listed(nodes, ports, _node_type, bundled_ports))

    # Node-config rules; each is a no-op when its config key is absent.
    checks.extend(_validate_review_survey_spec(nodes, _node_type))
    checks.extend(validate_review_answer_keys(nodes, _node_type))
    checks.extend(_validate_required_secrets(nodes, _node_type))
    checks.extend(_validate_required_input_parameters(nodes, _node_type))
    checks.extend(_validate_phantom_mainflow_targets(nodes, _node_type))

    # Nested projects are validated too, and components are judged as publish bundles
    # them, with node ports merged.
    if project_dir is not None:
        nested = nested_project_checks(
            project_dir,
            nodes,
            _node_type,
            lambda child: validate_orchestration(child, validate_scripts=validate_scripts),
        )
        refused = any(refuses_bundle(c) for c in checks + nested)
        reported = {c.message for c in checks}
        checks.extend(bundled_port_checks(project_dir, _node_type, reported, file_refused=refused))
        checks.extend(nested)

    valid = not any(c.severity == "error" for c in checks)
    return ValidationReport(valid=valid, checks=checks)
