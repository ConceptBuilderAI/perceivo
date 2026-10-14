"""The one property-type vocabulary, and the strict check that enforces it.

Every property type a port declares (a manifest's ``request_data`` /
``response_data`` / ``artifact_*_schema``, a port's ``request_data_schema`` /
``response_data_schema``, its JSON-Schema ``contract`` / ``post_tool_contract``
and a node declaration's ``_schema_type``) is spelled in exactly this
vocabulary::

    string  integer  float  boolean  object  array

There are no aliases. ``number``, ``double``, ``int``, ``str``, ``bool``,
``dict``, ``list``, ``text`` and every other spelling are REJECTED with a
validator error naming the permitted types; nothing is silently rewritten.
The platform refuses the same spellings at its write boundary (HTTP 400) with
the same message wording.

The UI *widget* vocabulary (``text``, ``text_long``, ``select``, ``number``)
is a different source vocabulary, not a set of aliases: it is translated by its
own map in ``_orchestration/_nested_ports.py``. A manifest's ``hyperparameters``
block is likewise its own source vocabulary (Python annotation terms), judged
through :data:`HYPERPARAMETER_TYPE_MAP` exactly as the platform does. The SDK
validator parses no Python annotations or docstrings into property types, so it
carries no general annotation map.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

#: The permitted property types, in their documented order.
PERMITTED_PROPERTY_TYPE_ORDER: tuple[str, ...] = (
    "string",
    "integer",
    "float",
    "boolean",
    "object",
    "array",
)

#: The complete set of permitted property types. ``number`` is absent: the platform
#: requires choosing ``integer`` or ``float`` explicitly.
PERMITTED_PROPERTY_TYPES = frozenset(PERMITTED_PROPERTY_TYPE_ORDER)

#: A JSON-Schema list type (``["string", "null"]``) may add ``null`` to declare
#: the value nullable. ``null`` is not a property type on its own.
_NULLABLE_MARKER = "null"

#: The keys of a port dict whose value is a field map or a JSON-Schema envelope.
PORT_SCHEMA_KEYS: tuple[str, ...] = (
    "request_data_schema",
    "response_data_schema",
    "request_data",
    "response_data",
    "contract",
    "post_tool_contract",
    "artifact_input_schema",
    "artifact_output_schema",
)

#: The node ``config`` blocks whose ``declarations`` may carry ``_schema_type``.
DECLARATION_BLOCK_KEYS = ("input_parameters", "output_parameters")

#: The declaration key that holds a *property* type. The sibling ``type`` key is
#: the UI widget vocabulary and is not judged here.
SCHEMA_TYPE_KEY = "_schema_type"

_COMBINATOR_KEYS = ("anyOf", "oneOf", "allOf")

#: ``"type (optional)"`` / ``"type (required)"`` in the manifest shorthand.
_OPTIONALITY = re.compile(r"^(.+?)\((optional|required)\)\s*$", re.IGNORECASE)


def permitted_types_text() -> str:
    """Return ``"string, integer, float, boolean, object, array"`` for messages."""
    return ", ".join(PERMITTED_PROPERTY_TYPE_ORDER)


def invalid_property_type_message(value: Any, path: str = "") -> str:
    """The platform's ``InvalidPropertyTypeError`` message, word for word."""
    where = f" at {path}" if path else ""
    return f"Invalid property type {value!r}{where}. Permitted types are: {permitted_types_text()}."


def is_permitted_property_type(value: Any) -> bool:
    """True for a permitted type, or a JSON-Schema list of them (``null`` allowed)."""
    if isinstance(value, str):
        return value in PERMITTED_PROPERTY_TYPES
    if isinstance(value, list):
        members = [m for m in value if m != _NULLABLE_MARKER]
        return bool(members) and all(
            isinstance(m, str) and m in PERMITTED_PROPERTY_TYPES for m in members
        )
    return False


def _bad(value: Any) -> bool:
    """A declared type that is present and not permitted. ``None`` is absence."""
    return value is not None and not is_permitted_property_type(value)


def shorthand_type_token(value: str) -> str:
    """The type token of the ``name: "type - description"`` / ``"type (optional)"`` shorthand."""
    token = value.split(" - ", 1)[0]
    token = token.split("(", 1)[0]
    return token.strip()


def canonical_type(type_value: Any) -> Any:
    """The declared field type, as compared across an edge.

    There is one property vocabulary and no aliases: nothing here maps ``int``
    onto ``integer`` or ``number`` onto ``float``. Two different spellings are two
    different types, and the edge check reports the mismatch (the non-canonical
    spelling itself is reported by :func:`invalid_schema_types`). A string is read
    by its type token only, so the ``name: "type - description"`` shorthand
    compares as its type. Anything else (``None``, a JSON-Schema list type such as
    ``["string", "null"]``) is compared as given.
    """
    return shorthand_type_token(type_value) if isinstance(type_value, str) else type_value


def _join(path: str, part: Any) -> str:
    if isinstance(part, int):
        return f"{path}[{part}]"
    return f"{path}.{part}" if path else str(part)


def _is_envelope(schema: dict[str, Any]) -> bool:
    """A JSON-Schema node rather than a ``{field: spec}`` map."""
    return isinstance(schema.get("type"), (str, list)) or isinstance(schema.get("properties"), dict)


def _iter_subschemas(spec: dict[str, Any], path: str) -> Iterator[tuple[Any, str]]:
    """``(node, path)`` for each nested schema position of one JSON-Schema node."""
    props = spec.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            yield sub, _join(_join(path, "properties"), name)
    items = spec.get("items")
    if isinstance(items, dict):
        yield items, _join(path, "items")
    elif isinstance(items, list):
        for i, sub in enumerate(items):
            yield sub, _join(_join(path, "items"), i)
    extra = spec.get("additionalProperties")
    if isinstance(extra, dict):
        yield extra, _join(path, "additionalProperties")
    for combinator in _COMBINATOR_KEYS:
        options = spec.get(combinator)
        if isinstance(options, list):
            for i, sub in enumerate(options):
                yield sub, _join(_join(path, combinator), i)


def _iter_spec(spec: dict[str, Any], path: str) -> Iterator[tuple[str, Any]]:
    """``(path, type)`` for one JSON-Schema node, recursing into nested schemas."""
    if "type" in spec and not isinstance(spec["type"], dict):
        yield _join(path, "type"), spec["type"]
    for sub, sub_path in _iter_subschemas(spec, path):
        if isinstance(sub, dict):
            yield from _iter_spec(sub, sub_path)


def iter_schema_types(schema: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """``(path, type)`` for every property-type position in a stored schema.

    Three stored shapes are walked: a field map (``{"f": {"type": ...}, "g":
    "integer"}``, where the bare ``name: type`` shorthand is read by its type
    token), the list form (``[{"name": "f", "type": ...}]``) and a JSON-Schema
    envelope, recursed through ``properties`` / ``items`` /
    ``additionalProperties`` / ``anyOf`` / ``oneOf`` / ``allOf``.
    """
    if isinstance(schema, list):
        for i, item in enumerate(schema):
            if isinstance(item, dict):
                yield from _iter_spec(item, _join(path, i))
        return
    if not isinstance(schema, dict):
        return
    if _is_envelope(schema):
        yield from _iter_spec(schema, path)
        return
    for name, spec in schema.items():
        if isinstance(spec, dict):
            yield from _iter_spec(spec, _join(path, name))
        elif isinstance(spec, str):
            yield _join(path, name), shorthand_type_token(spec)


def invalid_schema_types(schema: Any, path: str = "") -> list[tuple[str, Any]]:
    """``[(path, value)]`` for every non-permitted type in a stored schema."""
    return [(p, v) for p, v in iter_schema_types(schema, path) if _bad(v)]


def invalid_port_schema_types(port: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every non-permitted type in a port dict's schema-bearing keys."""
    if not isinstance(port, dict):
        return []
    bad: list[tuple[str, Any]] = []
    for key in PORT_SCHEMA_KEYS:
        if port.get(key) is not None:
            bad.extend(invalid_schema_types(port[key], _join(path, key)))
    return bad


def invalid_declaration_schema_types(config: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every non-permitted ``_schema_type`` on a node config's declarations.

    Only ``_schema_type`` is judged; a declaration's ``type`` is the UI widget
    vocabulary.
    """
    bad: list[tuple[str, Any]] = []
    if not isinstance(config, dict):
        return bad
    for block_key in DECLARATION_BLOCK_KEYS:
        block = config.get(block_key)
        declarations = block.get("declarations") if isinstance(block, dict) else None
        if not isinstance(declarations, list):
            continue
        for i, decl in enumerate(declarations):
            if isinstance(decl, dict) and SCHEMA_TYPE_KEY in decl and _bad(decl[SCHEMA_TYPE_KEY]):
                bad.append(
                    (
                        _join(path, f"{block_key}.declarations[{i}].{SCHEMA_TYPE_KEY}"),
                        decl[SCHEMA_TYPE_KEY],
                    )
                )
    return bad


def _strict_value(raw: Any) -> Any:
    """Return *raw* with surrounding whitespace stripped and its spelling kept exact."""
    return raw.strip() if isinstance(raw, str) else raw


def manifest_field_type(spec: str) -> str:
    """The type of a ``"type"`` / ``"type (optional) - description"`` manifest field."""
    field_type = spec.split(" - ", 1)[0].strip()
    match = _OPTIONALITY.match(field_type)
    if match:
        field_type = match.group(1).strip()
    return field_type


def invalid_manifest_data_types(data_def: Any, path: str = "") -> list[tuple[str, Any]]:
    """Non-permitted types in a manifest ``request_data`` / ``response_data`` block.

    Reads the list form, the dict form and the ``name: "type - description"``
    shorthand; a field with no ``type`` defaults to ``string``.
    """
    if isinstance(data_def, list):
        return _invalid_manifest_data_list(data_def, path)
    bad: list[tuple[str, Any]] = []
    if not isinstance(data_def, dict):
        return bad
    for name, spec in data_def.items():
        value = _manifest_data_field_type(spec)
        if value is not _NOT_A_FIELD and _bad(value):
            bad.append((_join(path, name), value))
    return bad


#: A dict-form data entry the parser skips (neither a string nor a dict).
_NOT_A_FIELD = object()


def _manifest_data_field_type(spec: Any) -> Any:
    """The type a dict-form data field declares, or :data:`_NOT_A_FIELD`."""
    if isinstance(spec, str):
        return manifest_field_type(spec)
    if isinstance(spec, dict):
        return _strict_value(spec.get("type", "string"))
    return _NOT_A_FIELD


def _invalid_manifest_data_list(items: list[Any], path: str) -> list[tuple[str, Any]]:
    """Non-permitted types in the list form of a manifest data block."""
    bad: list[tuple[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            value = _strict_value(item.get("type", "string"))
            if _bad(value):
                bad.append((_join(path, item["name"]), value))
    return bad


def invalid_manifest_schema_types(schema_def: Any, path: str = "") -> list[tuple[str, Any]]:
    """Non-permitted types in a manifest ``artifact_*_schema`` block."""
    bad: list[tuple[str, Any]] = []
    if not isinstance(schema_def, dict):
        return bad
    for name, spec in schema_def.items():
        if isinstance(spec, str):
            value = _strict_value(spec)
        elif isinstance(spec, dict):
            value = _strict_value(spec.get("type", "string"))
        else:
            continue
        if _bad(value):
            bad.append((_join(path, name), value))
    return bad


#: A manifest ``hyperparameters`` ``type`` -> property type. The block is authored in
#: Python annotation terms, so this is a separate source vocabulary, not aliases.
HYPERPARAMETER_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "float",
    "bool": "boolean",
    "boolean": "boolean",
}


def invalid_manifest_hyperparameter_types(
    hyperparams: Any, path: str = "hyperparameters"
) -> list[tuple[str, Any]]:
    """Non-permitted ``type`` values in a manifest ``hyperparameters`` block.

    Only a dict definition's ``type`` is judged; an absent or null ``type`` takes
    the parser's default; the spelling is matched against
    :data:`HYPERPARAMETER_TYPE_MAP` case- and whitespace-insensitively. A bare
    default value (``lr: 0.01``) carries no declared type and is never an error.
    """
    bad: list[tuple[str, Any]] = []
    if not hyperparams or not isinstance(hyperparams, dict):
        return bad
    for name, definition in hyperparams.items():
        if not isinstance(definition, dict):
            continue
        raw = definition.get("type", "float")
        if raw is None:
            continue
        if str(raw).strip().lower() not in HYPERPARAMETER_TYPE_MAP:
            bad.append((_join(_join(path, name), "type"), raw))
    return bad
