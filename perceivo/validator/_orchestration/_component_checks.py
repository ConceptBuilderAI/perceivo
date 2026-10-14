"""Per-component checks of a project directory, with or without its scripts."""

from __future__ import annotations

from typing import Any, Optional

from .._component import validate_source
from .._manifest import parse_manifest
from .._report import ValidationCheck
from .._unreadable_files import unreadable_manifest_check, unreadable_script_check

#: The manifest checks that name a refusal export makes too.
_EXPORT_REFUSALS = ("MAN-EMPTY-", "MAN-PARSE-", "MAN-FORMAT-", "MAN-FIELD-")


def component_checks(loaded: dict[str, Any], validate_scripts: bool) -> list[ValidationCheck]:
    """Each component's checks, with ids prefixed by the component's name."""
    checks: list[ValidationCheck] = []
    for comp in loaded.get("components", []):
        for check in _checks(comp, validate_scripts):
            check.id = f"{comp.get('name', 'unknown')}/{check.id}"
            checks.append(check)
    return checks


def _checks(comp: dict[str, Any], validate_scripts: bool) -> list[ValidationCheck]:
    if comp.get("script_unreadable"):
        # Export refuses it in either mode, and its script is "" below.
        return [unreadable_script_check(comp["script_unreadable"])]
    if not validate_scripts:
        # Export reads every manifest, so one it refuses is refused unjudged too.
        return manifest_refusals(comp.get("manifest_yaml"), comp.get("manifest_unreadable"))
    script = comp.get("script", "")
    if not script:
        return []
    return validate_source(
        script=script,
        component_type=comp.get("component_type", ""),
        manifest_yaml=comp.get("manifest_yaml"),
        ports=comp.get("ports"),
        manifest_unreadable=comp.get("manifest_unreadable"),
    ).checks


def manifest_refusals(
    manifest_yaml: Optional[str], unreadable: Optional[str]
) -> list[ValidationCheck]:
    """The manifest checks validate_source makes for what export refuses, and no others."""
    if unreadable:
        return [unreadable_manifest_check(unreadable)]
    if manifest_yaml is None:
        return []
    _, checks = parse_manifest(manifest_yaml, None)
    return [check for check in checks if check.id.startswith(_EXPORT_REFUSALS)]
