"""Component validation: script, manifest, and interface checks.

Runs offline, without database access.
"""

from __future__ import annotations

import ast
from typing import Any, Callable, Optional

from ._absent_manifest import report_absent_manifest as _report_absent_manifest
from ._component_tables import (
    _BASE_HOMES,
    ALL_COMPONENT_TYPES,
    CLASS_ENTRY_TYPES,
    COMPONENT_BASES,
    COMPONENT_REQUIRED_METHODS,
    DECORATOR_NAMES,
    unknown_component_type_message,
)
from ._declaration_truth import ORCHESTRATION_TYPES as _ORCHESTRATION_TYPES
from ._declaration_truth import validate_declaration_truth as _validate_declaration_truth
from ._manifest import parse_manifest
from ._port_rules import _manifest_ports, validate_port_rules
from ._report import ValidationCheck, ValidationReport
from ._sdk_names import unresolved_sdk_names
from ._security import run_security_checks
from ._unreadable_files import unreadable_manifest_check, unreadable_script_check


def validate_source(
    *,
    script: str,
    component_type: Optional[str],
    manifest_yaml: Optional[str] = None,
    ports: Optional[list[dict[str, Any]]] = None,
    fsm_states: Optional[list[str]] = None,
    ports_recorded: bool = False,
    script_unreadable: Optional[str] = None,
    manifest_unreadable: Optional[str] = None,
) -> ValidationReport:
    """Validate a component held in memory.

    Runs every static check that can be performed locally without database access.

    Args:
        script: Python source code of the component.
        component_type: Component type (AGENT, TOOL, FSM, etc.).
        manifest_yaml: Optional YAML manifest string.
        ports: Optional list of port dicts (from manifest or separate).
        fsm_states: Optional list of FSM state names.
        ports_recorded: *ports* are the rows an imported component's record
            holds. They are the rows the component is created with, so an empty
            list stands rather than falling back to the manifest, and a row
            that is not a port is refused.
        script_unreadable: Why the script file could not be read, when it could
            not. No other check runs then: *script* is not what export reads,
            and the script may be what names the type.
        manifest_unreadable: Why the manifest file could not be read, when it
            could not; *manifest_yaml* is then None.

    Returns:
        A :class:`ValidationReport` with all findings.
    """
    checks: list[ValidationCheck] = []
    counters: dict[str, int] = {}

    def add(
        prefix: str,
        severity: str,
        category: str,
        title: str,
        message: str,
        *,
        line: Optional[int] = None,
        suggestion: Optional[str] = None,
    ) -> None:
        seq = counters.get(prefix, 0) + 1
        counters[prefix] = seq
        checks.append(
            ValidationCheck(
                id=f"{prefix}-{seq:03d}",
                severity=severity,
                category=category,
                title=title,
                message=message,
                line=line,
                suggestion=suggestion,
            )
        )

    if script_unreadable:
        return ValidationReport(valid=False, checks=[unreadable_script_check(script_unreadable)])

    ctype = (component_type or "").upper().strip()

    if not ctype:
        # An empty ctype means no type-specific rule ran, so it cannot be reported clean.
        add(
            "VAL-TYPE",
            "error",
            "interface",
            "Component type not determined",
            "No component type was given, and neither the manifest ('extension_type' or "
            "'category') nor the script (a component decorator or base class) names exactly "
            "one, so no type-specific rule could run.",
            suggestion="Add 'extension_type: <TYPE>' or 'category: <TYPE>' to the manifest, "
            "or pass component_type explicitly.",
        )
    elif ctype not in ALL_COMPONENT_TYPES:
        add(
            "VAL-TYPE",
            "error",
            "interface",
            "Invalid component type",
            unknown_component_type_message(component_type),
        )

    if manifest_unreadable:
        # The only MAN-FORMAT here: no manifest text means none is parsed below.
        checks.append(unreadable_manifest_check(manifest_unreadable))

    script = _sanitize_script(script)

    if not script.strip():
        # REVIEW components may have no script
        if ctype != "REVIEW":
            add("VAL-SCRIPT", "error", "interface", "Script is empty", "Component script is empty.")
            return ValidationReport(valid=False, checks=checks)
        return ValidationReport(valid=True, checks=checks)

    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        add(
            "VAL-SYNTAX",
            "error",
            "syntax",
            "Syntax error",
            f"Python syntax error: {exc}",
            line=getattr(exc, "lineno", None),
            suggestion="Fix the syntax error before proceeding.",
        )
        return ValidationReport(valid=False, checks=checks)

    checks.extend(run_security_checks(tree))

    for line, error in unresolved_sdk_names(tree):
        add(
            "VAL-IMPORT",
            "error",
            "interface",
            "SDK name does not resolve",
            f"The component would fail when loaded: {error}",
            line=line,
            suggestion="Use a name the installed perceivo SDK exports.",
        )

    entry_class_name, is_decorator = _find_entry_class(tree)
    _validate_entry_point(tree, entry_class_name, is_decorator, ctype, add)

    record_error = _recorded_rows_error(ports) if ports_recorded else None
    if record_error:
        add(
            "VAL-RECORD",
            "error",
            "manifest",
            "Component record cannot be exported",
            f"The component record cannot be exported: {record_error}",
            suggestion="Re-import the component, or edit its manifest so the manifest owns the ports.",
        )
    manifest_ports, declared, parsed_manifest = _collect_manifest_ports(
        manifest_yaml, component_type, ports, checks, recorded=ports_recorded
    )
    # An empty port list is still judged: every rule is a count rule with a definite
    # result at zero. A type VAL-TYPE refuses is not judged again by its ports.
    if declared and not record_error and ctype in ALL_COMPONENT_TYPES:
        checks.extend(validate_port_rules(ctype, manifest_ports, fsm_states))

    # Runs only against a parsed manifest; with no manifest at all,
    # VAL-UNVERIFIABLE-MANIFEST applies instead if the script uses a contract.
    if not manifest_yaml and ctype in _ORCHESTRATION_TYPES:
        _report_absent_manifest(tree, (component_type or "").upper().strip(), add)
    _validate_declaration_truth(tree, parsed_manifest, ctype, add, lambda: len(checks))

    valid = not any(c.severity == "error" for c in checks)
    return ValidationReport(valid=valid, checks=checks)


def _sanitize_script(script: str) -> str:
    """Strip BOM, null bytes, and normalize line endings."""
    if script.startswith("\ufeff"):
        script = script[1:]
    script = script.replace("\x00", "")
    script = script.replace("\r\n", "\n").replace("\r", "\n")
    return script


def _find_entry_class(tree: ast.Module) -> tuple[Optional[str], bool]:
    """Find the component entry class from an AST.

    Returns:
        A ``(class_name, is_decorator)`` tuple; ``class_name`` is None if not found.
    """
    decorated = _find_decorated_function(tree)
    if decorated is not None:
        return decorated.name, True

    entry = _find_entry_class_assignment(tree)
    if entry is not None:
        return entry, False

    return None, False


def _find_entry_class_assignment(tree: ast.Module) -> Optional[str]:
    """Return the class name from an ``ENTRY_CLASS = "Name"`` assignment, if any.

    Only a string constant counts. The orchestration sandbox resolves the entry
    point with ``getattr(module, module.ENTRY_CLASS)``, so a class object or an
    instance raises ``TypeError: attribute name must be string`` at load. A
    validator that accepted those forms would pass a component the runtime
    cannot start; :func:`_entry_class_assignment_form` reports them instead.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _assigns_entry_class(node):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def _entry_class_assignment_form(tree: ast.Module) -> Optional[str]:
    """Describe a non-string ``ENTRY_CLASS`` assignment, or None if there is none.

    Returns the source text of the offending value so the error can quote it.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _assigns_entry_class(node):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        return ast.unparse(node.value)
    return None


def _entry_class_bound_name(tree: ast.Module) -> Optional[str]:
    """The class name of an ``ENTRY_CLASS = SomeClass`` assignment, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _assigns_entry_class(node):
            if isinstance(node.value, ast.Name):
                return node.value.id
    return None


def _assigns_entry_class(node: ast.Assign) -> bool:
    """Return True if any assignment target is the ``ENTRY_CLASS`` name."""
    return any(
        isinstance(target, ast.Name) and target.id == "ENTRY_CLASS" for target in node.targets
    )


def _validate_entry_point(
    tree: ast.Module,
    entry_class_name: Optional[str],
    is_decorator: bool,
    ctype: str,
    add: Callable[..., None],
) -> None:
    """Validate the component entry point (decorator or ENTRY_CLASS)."""
    if not entry_class_name:
        bound_name = _entry_class_bound_name(tree)
        if bound_name is not None and ctype in CLASS_ENTRY_TYPES:
            _validate_class_entry(tree, bound_name, ctype, add)
            return
        bad_form = _entry_class_assignment_form(tree)
        if bad_form is not None:
            # The assignment is there; it is the wrong form. Saying "no entry
            # point" would send the author looking for a line that is present.
            add(
                "VAL-ENTRY",
                "error",
                "interface",
                "ENTRY_CLASS is not a string",
                f"ENTRY_CLASS = {bad_form} assigns a class or an instance. The "
                "runtime resolves the entry point by name and rejects any other "
                "form.",
                suggestion=f'Write ENTRY_CLASS = "{bad_form.removesuffix("()")}" instead.',
            )
            return
        add(
            "VAL-ENTRY",
            "error",
            "interface",
            "No component entry point",
            "No component entry point found. Use a @perceivo decorator or define ENTRY_CLASS.",
            suggestion=(
                "Use @perceivo.agent, @perceivo.tool, etc. on an async function, "
                'or add ENTRY_CLASS = "MyClassName" at module level.'
            ),
        )
        return
    if not is_decorator:
        _validate_class_entry(tree, entry_class_name, ctype, add)
        return
    _validate_decorator_entry(tree, add)


def _validate_class_entry(
    tree: ast.Module,
    entry_class_name: str,
    ctype: str,
    add: Callable[..., None],
) -> None:
    """Validate a class-based entry point: existence, base class, and methods."""
    class_map = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    if entry_class_name not in class_map:
        add(
            "VAL-ENTRY",
            "error",
            "interface",
            "ENTRY_CLASS not found",
            f"ENTRY_CLASS '{entry_class_name}' is not defined as a class in the script.",
        )
        return
    cls = class_map[entry_class_name]
    _validate_base_class(cls, entry_class_name, ctype, add)
    _validate_required_methods(cls, ctype, add)


def _validate_base_class(
    cls: ast.ClassDef,
    entry_class_name: str,
    ctype: str,
    add: Callable[..., None],
) -> None:
    """Warn if the entry class does not inherit the expected perceivo base."""
    expected_base = COMPONENT_BASES.get(ctype)
    if not expected_base:
        return
    bases = _get_base_names(cls)
    if expected_base in bases:
        return
    add(
        "VAL-BASE",
        "warning",
        "interface",
        "Base class mismatch",
        f"Class '{entry_class_name}' does not inherit from "
        f"{_BASE_HOMES.get(ctype, 'perceivo.primitives')}.{expected_base}. "
        f"Found bases: {', '.join(bases) or '(none)'}",
    )


def _validate_required_methods(
    cls: ast.ClassDef,
    ctype: str,
    add: Callable[..., None],
) -> None:
    """Report required methods missing from the entry class."""
    methods = {
        node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for method in COMPONENT_REQUIRED_METHODS.get(ctype, []):
        if method not in methods:
            add(
                "VAL-METHOD",
                "error",
                "interface",
                "Missing required method",
                f"Missing required method: {method}()",
                line=getattr(cls, "lineno", None),
            )


def _validate_decorator_entry(tree: ast.Module, add: Callable[..., None]) -> None:
    """Verify a decorator-based entry point decorates an async function."""
    decorated_func = _find_decorated_function(tree)
    if decorated_func and not isinstance(decorated_func, ast.AsyncFunctionDef):
        add(
            "VAL-ASYNC",
            "error",
            "interface",
            "Decorator requires async function",
            "Perceivo decorators must be applied to async functions.",
            line=getattr(decorated_func, "lineno", None),
        )


def _collect_manifest_ports(
    manifest_yaml: Optional[str],
    component_type: Optional[str],
    ports: Optional[list[dict[str, Any]]],
    checks: list[ValidationCheck],
    *,
    recorded: bool = False,
) -> tuple[list[dict[str, Any]], bool, Optional[dict[str, Any]]]:
    """Parse the manifest (appending its checks) and resolve the port list.

    The second element says whether a port declaration was available at all: an
    explicit list or a manifest that parsed. An empty list from a manifest that
    parsed is a real answer ("declares no ports") and the rules must judge it; an
    empty list because there was nothing to read is not.
    """
    manifest_ports = ports or []
    # None is no manifest file; an empty file is a manifest, and export refuses it.
    if manifest_yaml is None:
        return manifest_ports, bool(ports), None

    parsed, manifest_checks = parse_manifest(manifest_yaml, component_type)
    checks.extend(manifest_checks)

    if parsed and not manifest_ports and not recorded:
        manifest_ports = _manifest_ports(parsed)

    return manifest_ports, parsed is not None, parsed


class RecordRefusal(str):
    """Why export refuses a component record; stands in for its port rows."""


def _recorded_rows_error(rows: Any) -> Optional[str]:
    """Why the recorded rows would fail the exporter, or None."""
    from pydantic import ValidationError

    from .._bundle.schema import PortExportData

    if isinstance(rows, RecordRefusal):
        return str(rows)
    if not isinstance(rows, list):
        return f"its ports are {type(rows).__name__}, not a list of port rows"
    for index, row in enumerate(rows):
        try:
            PortExportData.model_validate(row)
        except ValidationError as exc:
            return f"port row {index}: {exc.errors()[0]['msg']}"
    return None


def _is_perceivo_decorator(dec: ast.expr) -> bool:
    """Check if a decorator is a perceivo component decorator.

    Three spellings are one decorator: ``@perceivo.agent`` (the top-level
    re-export), ``@perceivo.primitives.agent`` and a bare ``@agent`` imported
    from :mod:`perceivo.primitives`.
    """
    if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
        if dec.value.id == "perceivo" and dec.attr in DECORATOR_NAMES:
            return True
    if (
        isinstance(dec, ast.Attribute)
        and dec.attr in DECORATOR_NAMES
        and isinstance(dec.value, ast.Attribute)
        and dec.value.attr == "primitives"
        and isinstance(dec.value.value, ast.Name)
        and dec.value.value.id == "perceivo"
    ):
        return True
    if isinstance(dec, ast.Name) and dec.id in DECORATOR_NAMES:
        return True
    if isinstance(dec, ast.Call):
        return _is_perceivo_decorator(dec.func)
    return False


def _find_decorated_function(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the function decorated with a perceivo decorator."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if _is_perceivo_decorator(dec):
                    return node
    return None


def _get_base_names(cls: ast.ClassDef) -> set[str]:
    """Extract base class names from a class definition."""
    names = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names
