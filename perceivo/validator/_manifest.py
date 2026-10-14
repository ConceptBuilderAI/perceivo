"""Manifest YAML parsing and validation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional

import yaml

from ._component_type import manifest_declared_type
from ._port_rules import _MANIFEST_PORT_SECTIONS, _manifest_section_items
from ._report import ValidationCheck
from ._type_names import (
    HYPERPARAMETER_TYPE_MAP,
    invalid_manifest_data_types,
    invalid_manifest_hyperparameter_types,
    invalid_manifest_schema_types,
    invalid_property_type_message,
    permitted_types_text,
)


def parse_manifest(
    manifest_yaml: Optional[str],
    component_type: Optional[str],
) -> tuple[Optional[dict[str, Any]], list[ValidationCheck]]:
    """Parse and validate a component manifest YAML string.

    Returns:
        Tuple of (parsed_manifest_dict, list_of_checks).
        parsed_manifest is None if YAML is invalid.
    """
    checks: list[ValidationCheck] = []
    _counters: dict[str, Any] = {}

    def _add(
        prefix: str,
        severity: str,
        title: str,
        message: str,
        suggestion: str | None = None,
    ) -> None:
        seq = _counters.get(prefix, 0) + 1
        _counters[prefix] = seq
        checks.append(
            ValidationCheck(
                id=f"{prefix}-{seq:03d}",
                severity=severity,
                category="manifest",
                title=title,
                message=message,
                suggestion=suggestion,
            )
        )

    if not manifest_yaml or not manifest_yaml.strip():
        _add("MAN-EMPTY", "error", "Manifest is empty", "No manifest YAML provided.")
        return None, checks

    try:
        parsed = yaml.safe_load(manifest_yaml)
    except yaml.YAMLError as exc:
        _add(
            "MAN-PARSE",
            "error",
            "Manifest parse error",
            f"Invalid YAML: {exc}",
            suggestion="Check YAML syntax (indentation, colons, quotes).",
        )
        return None, checks

    if not isinstance(parsed, dict):
        _add(
            "MAN-FORMAT",
            "error",
            "Invalid manifest format",
            "Manifest must be a YAML mapping (dict), not a list or scalar.",
            suggestion="Write the manifest as key: value lines, starting with name: and "
            "extension_type:.",
        )
        return None, checks

    refusal = _export_refusal(manifest_yaml, parsed)
    if refusal:
        _add(
            "MAN-FIELD",
            "error",
            "Manifest field export refuses",
            f"Export cannot build a component from this manifest: {refusal}",
            suggestion="Port sections and fsm_states are lists; config is a mapping or null.",
        )

    ext_type = manifest_declared_type(parsed) or ""
    # ``component_type`` is None when the caller could not derive one; an unnamed type
    # is reported by validate_component, not crashed on here.
    ctype = (component_type or "").upper().strip()

    if ext_type and ext_type != ctype:
        # Name the key the type came from, since a bundle-shaped manifest carries only ``category``.
        key = "extension_type" if parsed.get("extension_type") else "category"
        _add(
            "MAN-TYPE",
            "error",
            "Type mismatch",
            f"Manifest {key} '{ext_type}' does not match component type '{component_type}'.",
        )

    # A top-level ``ports:`` list is not a manifest form: the platform reads only port
    # sections, so the component would be judged as portless.
    if "ports" in parsed:
        _add(
            "MAN-PORTS",
            "error",
            "Unsupported ports form",
            "The manifest declares a top-level 'ports:' list. Ports are declared "
            "in sections: input_ports, output_ports, tool_ports, policy_ports, "
            "state_ports, review_ports, external_in_port, external_out_port.",
            suggestion="Move each port into the section for its port_type, keyed port_name.",
        )

    for path, value in manifest_property_type_errors(parsed):
        noun, vocabulary = _property_type_vocabulary(path)
        note = _NUMBER_NOTE if _declares_number(value) else ""
        _add(
            "MAN-PROPTYPE",
            "error",
            "Invalid property type",
            invalid_property_type_message(value, path),
            suggestion=f"Spell the {noun} as one of: {vocabulary}.{note}",
        )

    return parsed, checks


def _export_refusal(manifest_yaml: str, parsed: dict[str, Any]) -> Optional[str]:
    """Why export refuses *parsed*, or None when it builds a component from it."""
    # Deferred: perceivo._bundle imports this package, so a module-level import is circular.
    from pydantic import ValidationError

    from .._bundle.component import ITERATED_KEYS, BundleConfigError, manifest_component

    try:
        manifest_component(manifest_yaml, parsed, name="component", script="")
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return f"{exc.title} field {where}: {first['msg']}"
    except BundleConfigError as exc:
        return str(exc)
    except TypeError as exc:
        # The first key export iterates that holds no iterable is the one that raised.
        for key in ITERATED_KEYS:
            value = parsed.get(key)
            if value and not isinstance(value, Iterable):
                return f"{key} must be a list, not {type(value).__name__}"
        return str(exc)
    return None


#: A port item's data blocks.
_DATA_BLOCKS = ("request_data", "response_data")
#: A port item's artifact schema blocks.
_SCHEMA_BLOCKS = ("artifact_input_schema", "artifact_output_schema")
#: The component-level field map a START component declares.
_REQUEST_DATA_SCHEMA = "request_data_schema"
#: A training manifest's hyperparameter block.
_HYPERPARAMETERS = "hyperparameters"


#: Appended to the MAN-PROPTYPE suggestion when the declared type is 'number'.
_NUMBER_NOTE = (
    " 'number' is not a property type: declare 'integer' for a whole count or 'float' for a real."
)


def _property_type_vocabulary(path: str) -> tuple[str, str]:
    """The noun and permitted spellings for an invalid property type at ``path``."""
    if path.split(".", 1)[0] == _HYPERPARAMETERS:
        return "hyperparameter type", ", ".join(dict.fromkeys(HYPERPARAMETER_TYPE_MAP.values()))
    return "type", permitted_types_text()


def _declares_number(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "number"


def manifest_property_type_errors(parsed: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return ``[(path, value)]`` for every manifest property type outside the vocabulary.

    The platform rejects these when the component is created or imported. There
    are no aliases, so ``number``, ``int``, ``str``, ``bool`` and ``double`` are each
    an error.

    Covered: each port's data and artifact schema blocks; the component-level
    ``request_data_schema``; and ``hyperparameters.<name>.type``, which has its own
    vocabulary (``HYPERPARAMETER_TYPE_MAP``: ``int``/``str``/``bool`` are accepted
    there, ``number``/``double``/``object``/``array`` are not).
    """
    bad: list[tuple[str, Any]] = []
    for section in _MANIFEST_PORT_SECTIONS:
        for index, item in enumerate(_manifest_section_items(parsed, section)):
            if isinstance(item, dict):
                bad.extend(_port_item_property_type_errors(section, index, item))
    bad.extend(invalid_manifest_data_types(parsed.get(_REQUEST_DATA_SCHEMA), _REQUEST_DATA_SCHEMA))
    bad.extend(
        invalid_manifest_hyperparameter_types(parsed.get(_HYPERPARAMETERS), _HYPERPARAMETERS)
    )
    return bad


def _port_item_property_type_errors(
    section: str, index: int, item: dict[str, Any]
) -> list[tuple[str, Any]]:
    """Non-permitted types in one port item's data and artifact schema blocks."""
    name = item.get("port_name") or item.get("key") or item.get("name")
    where = f"{section}.{name}" if isinstance(name, str) and name else f"{section}[{index}]"
    bad: list[tuple[str, Any]] = []
    for block in _DATA_BLOCKS:
        bad.extend(invalid_manifest_data_types(item.get(block), f"{where}.{block}"))
    for block in _SCHEMA_BLOCKS:
        bad.extend(invalid_manifest_schema_types(item.get(block), f"{where}.{block}"))
    return bad
