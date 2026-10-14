"""Check a run's input against the request data its START declares.

The declaration is a manifest block, so it is read the way the platform's
manifest parser reads one: a field is required unless its string form carries
``(optional)``, its mapping sets ``required: false``, or its list item sets
``optional: true``. The port value is ``external_in_port``, or
``external_in_ports`` when ``external_in_port`` is missing or falsy (null, an
empty mapping, list or string, 0 or false); either may be one port or a list,
and the first mapping port in it is the one checked. When there is no such port
or it declares no fields, the manifest's top-level ``request_data_schema`` is the
declaration. A port value that is neither falsy nor iterable, such as a non-zero
number, true or a date, declares nothing, whatever ``request_data_schema`` holds.
A START that declares no fields accepts anything.

The platform does not enforce this declaration when a run starts, so the local
runner is stricter by design: a key the START does not declare, or a required
key that is absent, fails the run before START runs, with a message naming the
declared keys. The check is in the runner, so ``run_orchestration`` and the CLI
``run`` command behave the same.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Optional

from ..validator._orchestration._manifest_overlay import parse_data_fields
from .loader import load_manifest
from .types import NodeShell


class _NoDeclaration(Exception):
    pass


def _first_port_request_data(manifest: Mapping[str, Any]) -> Any:
    # The platform's manifest parser accepts one port or a list under either key,
    # keeps only mapping entries, and reads a START's input from the first one.
    ports = manifest.get("external_in_port") or manifest.get("external_in_ports") or []
    if isinstance(ports, Mapping):
        ports = [ports]
    if not isinstance(ports, Iterable):
        # The platform's parser raises on a port value it cannot iterate, and the
        # START contract is then abandoned before its request_data_schema fallback.
        raise _NoDeclaration
    first = next((port for port in ports if isinstance(port, Mapping)), None)
    return first.get("request_data") if first is not None else None


def _declaration(shell: NodeShell) -> tuple[dict[str, Any], str]:
    if not shell.manifest_path:
        return {}, ""
    manifest = load_manifest(shell.manifest_path)
    try:
        fields = parse_data_fields(_first_port_request_data(manifest))
    except _NoDeclaration:
        return {}, ""
    if fields:
        key = "external_in_port" if manifest.get("external_in_port") else "external_in_ports"
        return fields, f"{key}.request_data"
    # The platform falls back to the component-level schema whenever the parsed
    # port fields are empty, so a port that declares nothing does not hide it.
    return parse_data_fields(manifest.get("request_data_schema")), "request_data_schema"


def declared_fields(shell: NodeShell) -> dict[str, Any]:
    """The fields ``shell``'s START declares, each with its type and whether it is required."""
    return _declaration(shell)[0]


def start_input_error(shell: NodeShell, input_data: Mapping[str, Any]) -> Optional[str]:
    """The reason ``input_data`` does not fit ``shell``'s declaration, or ``None``."""
    fields, source = _declaration(shell)
    if not fields:
        return None
    unknown = sorted(str(key) for key in input_data if key not in fields)
    missing = sorted(
        name for name, spec in fields.items() if spec["required"] and name not in input_data
    )
    if not unknown and not missing:
        return None
    problems = []
    if unknown:
        problems.append(f"unknown key(s) {', '.join(unknown)}")
    if missing:
        problems.append(f"missing required key(s) {', '.join(missing)}")
    required = sorted(name for name, spec in fields.items() if spec["required"])
    return (
        f"Input does not match START '{shell.name}' {source}: "
        f"{'; '.join(problems)}. Declared keys: {', '.join(sorted(fields))}"
        f" (required: {', '.join(required) or 'none'})."
    )
