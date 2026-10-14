"""The request and response fields a nested orchestration's port carries.

On import the platform gives every child orchestration one generated
``orchestration`` port, and a parent's edges into and out of the nested node
are judged against it. Each half is the first of these that declares anything:

* request half (``request_data_schema``): the child START node's
  ``input_parameters.declarations``; else its OUT port's
  ``response_data_schema``; else that port's ``contract`` as flat fields.
* response half (``response_data_schema``): the child END node's
  ``output_parameters.declarations``; else its IN port's
  ``request_data_schema``; else that port's ``contract`` as flat fields.

The artifact halves are stamped whatever the declarations say:

* ``artifact_type_input`` / ``artifact_input_schema``: the child START OUT
  port's ``artifact_type_output`` / ``artifact_output_schema``;
* ``artifact_type_output`` / ``artifact_output_schema``: the child END IN
  port's ``artifact_type_input`` / ``artifact_input_schema``.

The generated port is a new port row, so it declares no
``artifact_type_constraint``, ``response_data_passthrough``,
``response_data_passthrough_except`` or ``response_data_contract``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .._type_names import SCHEMA_TYPE_KEY, is_permitted_property_type

# UI widget type -> property type. ``text_long``, ``select`` and ``number`` are
# editors, not types; an unlisted spelling reaches the strict ORCH-PROPTYPE check.
_TYPE_MAP = {
    "text": "string",
    "text_long": "string",
    "select": "string",
    "multi_select": "string",
    "secret": "string",
    "platform_database": "string",
    "number": "float",
    "integer": "integer",
    "float": "float",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}

# Keys a JSON Schema object contract may carry at its top level.
_CONTRACT_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties", "title", "description"}
)

_REQUEST = "request_data_schema"
_RESPONSE = "response_data_schema"

#: Fields the generated port never declares; whatever the parent's copy carried
#: is dropped.
_CLEARED = (
    "artifact_type_constraint",
    "response_data_passthrough",
    "response_data_passthrough_except",
    "response_data_contract",
)


def declaration_property_type(decl: dict[str, Any], default: str) -> str:
    """Return the property type a declaration contributes.

    An explicit, permitted ``_schema_type`` wins; that is how a numeric editor
    declares a whole count. Otherwise the widget ``type`` is translated through
    :data:`_TYPE_MAP`; a spelling the map does not know passes through lowercased.
    """
    schema_type = decl.get(SCHEMA_TYPE_KEY)
    if isinstance(schema_type, str) and is_permitted_property_type(schema_type):
        return schema_type
    raw = decl.get("type", default)
    raw_type = (raw if isinstance(raw, str) else default).lower()
    return _TYPE_MAP.get(raw_type, raw_type)


def declarations_to_contract(decls: Any) -> Optional[dict[str, Any]]:
    """A UI declaration list as a JSON Schema contract, or None when it names nothing."""
    if not decls or not isinstance(decls, list):
        return None
    props: dict[str, Any] = {}
    required: list[str] = []
    for decl in decls:
        if not isinstance(decl, dict):
            continue
        name = decl.get("name", "")
        if not name:
            continue
        props[name] = {"type": declaration_property_type(decl, "string")}
        if decl.get("required", False):
            required.append(name)
    if not props:
        return None
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def contract_to_fields(schema: Any) -> Any:
    """A JSON Schema object contract as flat ``{field: definition}`` fields.

    Each property is marked ``required`` exactly as the contract marks it. An
    open or empty object declares no fields (None); a value already in flat form
    is returned unchanged.
    """
    if not isinstance(schema, dict) or not schema:
        return None
    if schema.get("type") != "object" or not set(schema) <= _CONTRACT_KEYS:
        return schema
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    required = set(schema.get("required") or [])
    return {
        name: {**(prop if isinstance(prop, dict) else {}), "required": name in required}
        for name, prop in props.items()
    }


def _declared_fields(node: Optional[dict[str, Any]], section: str) -> Any:
    config = (node or {}).get("config") or {}
    params = config.get(section) if isinstance(config, dict) else None
    decls = params.get("declarations") if isinstance(params, dict) else None
    contract = declarations_to_contract(decls)
    return contract_to_fields(contract) if contract else None


def _is_direction(port: dict[str, Any], direction: str) -> bool:
    declared = str(port.get("direction") or "").lower()
    if declared:
        return declared == direction
    return str(port.get("port_type") or "").lower() == direction


def _first_port(ports: list[dict[str, Any]], node_id: Any, direction: str) -> Optional[dict]:  # type: ignore[type-arg]
    for port in ports:
        if (
            isinstance(port, dict)
            and not port.get("virtual")
            and str(port.get("node_id")) == str(node_id)
            and _is_direction(port, direction)
        ):
            return port
    return None


def _first_node_of_type(loaded: dict[str, Any], wanted: str) -> Optional[dict[str, Any]]:
    types = loaded.get("component_types") or {}
    for node in loaded.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ctype = str(types.get(node.get("id")) or node.get("component_type") or "").upper()
        if ctype == wanted:
            return node
    return None


def _half_fields(
    node: dict[str, Any],
    section: str,
    ports: list[dict[str, Any]],
    direction: str,
    schema_key: str,
) -> Any:
    """Declarations first, then the boundary port's schema, then its contract."""
    fields = _declared_fields(node, section)
    if fields is not None:
        return fields
    port = _first_port(ports, node.get("id"), direction)
    if port is None:
        return None
    return port.get(schema_key) or contract_to_fields(port.get("contract"))


def _boundary_artifact(
    declared: bool, port: Optional[dict[str, Any]], type_key: str, schema_key: str
) -> tuple[Any, Any]:
    """Return ``(artifact_type, artifact_schema)`` for a boundary port.

    Declarations leave the artifact unset and the port fills it in anyway, so a
    port that exists always supplies both.
    """
    artifact_type = artifact_schema = None
    if not declared and port is not None:
        artifact_type = port.get(type_key)
        artifact_schema = port.get(schema_key)
    if port is not None and not artifact_type:
        artifact_type = port.get(type_key)
        artifact_schema = port.get(schema_key)
    return artifact_type, artifact_schema


def child_port_meta(loaded: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Every field the platform stamps on a loaded child's ``orchestration`` port.

    None when the child has no START or no END; the platform then generates no
    port.
    """
    start = _first_node_of_type(loaded, "START")
    end = _first_node_of_type(loaded, "END")
    if start is None or end is None:
        return None
    ports = loaded.get("ports") or []
    start_out = _first_port(ports, start.get("id"), "out")
    end_in = _first_port(ports, end.get("id"), "in")
    in_type, in_schema = _boundary_artifact(
        _declared_fields(start, "input_parameters") is not None,
        start_out,
        "artifact_type_output",
        "artifact_output_schema",
    )
    out_type, out_schema = _boundary_artifact(
        _declared_fields(end, "output_parameters") is not None,
        end_in,
        "artifact_type_input",
        "artifact_input_schema",
    )
    meta: dict[str, Any] = {
        _REQUEST: _half_fields(start, "input_parameters", ports, "out", _RESPONSE),
        "artifact_type_input": in_type,
        "artifact_input_schema": in_schema,
        _RESPONSE: _half_fields(end, "output_parameters", ports, "in", _REQUEST),
        "artifact_type_output": out_type,
        "artifact_output_schema": out_schema,
    }
    meta.update(dict.fromkeys(_CLEARED))
    return meta


def child_port_fields(loaded: dict[str, Any]) -> tuple[Any, Any]:
    """``(request_data_schema, response_data_schema)`` for a loaded child project."""
    meta = child_port_meta(loaded)
    if meta is None:
        return None, None
    return meta[_REQUEST], meta[_RESPONSE]


def _stamp(port: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        port.pop(key, None)
    else:
        port[key] = value


def apply_nested_port_fields(
    project_dir: str,
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    node_type: Callable[[str], str],
    load_child: Callable[[str], Optional[dict[str, Any]]],
) -> None:
    """Give each nested node's ports every field the platform generates.

    A node whose project does not load is left alone; the nested-project check
    reports it. The platform generates the port from the child and nothing
    else, so every field in ``child_port_meta`` replaces whatever the parent's
    copy carried, and a None field is removed.
    """
    base = Path(project_dir).resolve()
    for node in nodes:
        loaded = _load_nested_child(base, node, node_type, load_child)
        if loaded is None:
            continue
        meta = child_port_meta(loaded)
        if meta is None:
            meta = {_REQUEST: None, _RESPONSE: None}
        for port in _node_ports(ports, node.get("id")):
            for key, value in meta.items():
                _stamp(port, key, value)


def unresolved_nested_nodes(
    nodes: list[dict[str, Any]], node_type: Callable[[str], str]
) -> frozenset[str]:
    """Ids of ORCHESTRATION nodes that name no local ``project``.

    Such a node nests a child the SDK cannot see, typically a capability member
    referenced as ``@member:<name>`` and imported without its members, so the
    fields the platform would generate on its ports are unknown, not absent.
    """
    return frozenset(
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict)
        and node_type(str(node.get("id", ""))) == "ORCHESTRATION"
        and not node.get("project")
    )


def _load_nested_child(
    base: Path,
    node: Any,
    node_type: Callable[[str], str],
    load_child: Callable[[str], Optional[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    if not isinstance(node, dict) or node_type(str(node.get("id", ""))) != "ORCHESTRATION":
        return None
    declared = node.get("project")
    if not declared:
        return None
    child_dir = (base / str(declared)).resolve()
    return load_child(str(child_dir)) if child_dir.is_dir() else None


def _node_ports(ports: list[dict[str, Any]], node_id: Any) -> list[dict[str, Any]]:
    return [
        p
        for p in ports
        if isinstance(p, dict) and not p.get("virtual") and str(p.get("node_id")) == str(node_id)
    ]
