"""Overlay each component manifest's port declarations onto the graph's ports.

The platform treats the component manifest as the single source of truth for a
port's payload schemas and artifact types, and overlays it onto the port rows
before any edge is checked. A project's ``orchestration.yaml`` carries a mirror
of those ports, and the mirror can be stale: a policy component whose manifest
declares ``artifact_type_input: none`` may still say ``json`` in the mirror.
This module applies the same overlay so the SDK checks the ports the platform
checks.

A property type is carried exactly as the manifest spells it (surrounding
whitespace aside): there is no alias map, so ``number`` stays ``number`` and is
reported by the component's ``MAN-PROPTYPE`` check and the graph's
``ORCH-PROPTYPE`` check rather than rewritten.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from ._edges import port_direction

MANIFEST_PORT_SECTIONS = (
    "input_ports",
    "output_ports",
    "tool_ports",
    "state_ports",
    "policy_ports",
    "review_ports",
    "external_in_ports",
    "external_out_ports",
)

_OPTIONALITY = re.compile(r"^(.+?)\((optional|required)\)\s*$", re.IGNORECASE)


def _norm(type_name: Any) -> Any:
    """Return the declared type as authored, never mapped.

    Surrounding whitespace is ignored; the spelling itself is kept, so a
    non-canonical spelling reaches the port where the strict checks report it.
    """
    return type_name.strip() if isinstance(type_name, str) else type_name


def _str_field(spec: str) -> dict[str, Any]:
    """``"type"`` / ``"type (optional) - description"`` -> a field spec."""
    parts = spec.split(" - ", 1)
    field_type = parts[0].strip()
    required = True
    match = _OPTIONALITY.match(field_type)
    if match:
        field_type = match.group(1).strip()
        required = match.group(2).lower() != "optional"
    return {
        "type": _norm(field_type),
        "required": required,
        "description": parts[1].strip() if len(parts) > 1 else "",
    }


def parse_data_fields(data_def: Any) -> dict[str, Any]:
    """Parse a manifest ``request_data`` / ``response_data`` block."""
    fields: dict[str, Any] = {}
    if isinstance(data_def, list):
        for item in data_def:
            if isinstance(item, dict) and item.get("name"):
                fields[item["name"]] = {
                    "type": _norm(item.get("type", "string")),
                    "required": not item.get("optional", False),
                    "description": item.get("description", ""),
                }
        return fields
    if not isinstance(data_def, dict):
        return fields
    for name, spec in data_def.items():
        if isinstance(spec, str):
            fields[name] = _str_field(spec)
        elif isinstance(spec, dict):
            fields[name] = {
                "type": _norm(spec.get("type", "string")),
                "required": spec.get("required", True),
                "description": spec.get("description", ""),
            }
    return fields


def _parse_schema(schema_def: Any) -> dict[str, Any]:
    """Parse an artifact schema block."""
    schema: dict[str, Any] = {}
    if not isinstance(schema_def, dict):
        return schema
    for name, spec in schema_def.items():
        if isinstance(spec, str):
            schema[name] = {"type": _norm(spec)}
        elif isinstance(spec, dict):
            schema[name] = {"type": _norm(spec.get("type", "string"))}
            if spec.get("description"):
                schema[name]["description"] = spec["description"]
    return schema


def _contract(fields: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON Schema object contract from parsed fields."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in fields.items():
        if isinstance(spec, dict):
            properties[name] = {k: v for k, v in spec.items() if k != "required"}
            if spec.get("required", True):
                required.append(name)
    contract: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        contract["required"] = required
    return contract


def _parsed_ports(manifest_yaml: str) -> dict[str, dict[str, Any]]:
    """Index a manifest's sectioned ports by ``port_name``."""
    try:
        manifest = yaml.safe_load(manifest_yaml) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(manifest, dict):
        return {}
    by_key: dict[str, dict[str, Any]] = {}
    for section in MANIFEST_PORT_SECTIONS:
        entries = manifest.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("port_name"):
                by_key[str(entry["port_name"])] = entry
    return by_key


def _overlay(port: dict[str, Any], entry: dict[str, Any]) -> None:
    """Overlay one manifest port onto a graph port; a manifest value that is present wins."""
    request = parse_data_fields(entry.get("request_data"))
    if request:
        port["request_data_schema"] = request
        port["contract"] = _contract(request)
    response = parse_data_fields(entry.get("response_data"))
    if response:
        port["response_data_schema"] = response
        port["post_tool_contract"] = _contract(response)
    for field in ("artifact_type_input", "artifact_type_output"):
        if entry.get(field):
            port[field] = str(entry[field]).lower()
    for field in ("artifact_input_schema", "artifact_output_schema"):
        schema = _parse_schema(entry.get(field))
        if schema:
            port[field] = schema


def _set_direction(port: dict[str, Any]) -> None:
    """State the direction a port row's ``port_type`` implies, where it states none.

    It is stated in the graph's lower-case spelling, which the config-hop
    expansion matches a node's real ``in``/``out`` port by.
    """
    port.setdefault("direction", port_direction(port).lower())


def overlay_manifest_ports(manifest_yaml: str, node_id: str, ports: list[dict[str, Any]]) -> None:
    """Overlay *manifest_yaml*'s port declarations onto *node_id*'s ports in place.

    Ports are matched by key (``key``, else ``name``), as the platform matches
    them. A port the manifest does not declare, and a manifest that does not
    parse, leave the graph's ports unchanged; the platform skips the overlay
    in both cases too.

    Each port is also given the direction its ``port_type`` implies
    (:func:`_set_direction`), with no manifest too: the fold and the dataflow
    chain read it.
    """
    if not isinstance(ports, list):
        return
    by_key = _parsed_ports(manifest_yaml) if manifest_yaml else {}
    for port in ports:
        if not isinstance(port, dict) or str(port.get("node_id", "")) != node_id:
            continue
        entry = by_key.get(str(port.get("key") or port.get("name") or ""))
        if entry is not None:
            _overlay(port, entry)
        _set_direction(port)
