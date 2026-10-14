"""One property vocabulary, no aliases -- as the platform enforces it.

The permitted property types are ``string``, ``integer``, ``float``,
``boolean``, ``object`` and ``array``. The platform REJECTS every other
spelling at its write
boundary -- ``number``, ``int``, ``str``, ``bool``, ``double`` ... -- with
``Invalid property type '<v>' at <path>. Permitted types are: ...`` and rewrites
nothing. The SDK validator must reach the same answer: a non-canonical spelling is an
error naming the permitted types, never silently mapped onto a canonical one,
and two different spellings never compare equal across an edge.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from perceivo.validator import validate_orchestration
from perceivo.validator._contracts import (
    _json_artifact_schema_errors,
    validate_port_contract_match,
)
from perceivo.validator._manifest import parse_manifest
from perceivo.validator._orchestration import _manifest_overlay, _nested_ports
from perceivo.validator._orchestration._main import validate_graph
from perceivo.validator._type_names import (
    PERMITTED_PROPERTY_TYPE_ORDER,
    PERMITTED_PROPERTY_TYPES,
    canonical_type,
    invalid_declaration_schema_types,
    invalid_manifest_data_types,
    invalid_manifest_schema_types,
    invalid_port_schema_types,
    invalid_property_type_message,
    invalid_schema_types,
    is_permitted_property_type,
    permitted_types_text,
)

PERMITTED_TEXT = "Permitted types are: string, integer, float, boolean, object, array."

#: Non-canonical spellings the platform refuses at its write boundary.
LEGACY_SPELLINGS = [
    "number",
    "double",
    "int",
    "str",
    "bool",
    "dict",
    "list",
    "text",
    "Integer",
    "STRING",
]


def _errors(provider_type: Any, consumer_type: Any, *, shorthand: bool) -> list[str]:
    """Contract errors for one field, in string-shorthand or ``{"type": ...}`` form."""
    if shorthand:
        src = {"response_data_schema": {"x": provider_type}}
        tgt = {"request_data_schema": {"x": consumer_type}}
    else:
        src = {"response_data_schema": {"x": {"type": provider_type}}}
        tgt = {"request_data_schema": {"x": {"type": consumer_type}}}
    return validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")


# ── The vocabulary ────────────────────────────────────────────────────────


def test_vocabulary_is_pinned():
    # Mirrors the platform's PERMITTED_PROPERTY_TYPE_ORDER; change both or neither.
    assert PERMITTED_PROPERTY_TYPE_ORDER == (
        "string",
        "integer",
        "float",
        "boolean",
        "object",
        "array",
    )
    assert frozenset(PERMITTED_PROPERTY_TYPE_ORDER) == PERMITTED_PROPERTY_TYPES
    assert "number" not in PERMITTED_PROPERTY_TYPES


def test_no_alias_map_survives():
    import perceivo.validator._type_names as type_names

    assert not hasattr(type_names, "DATA_TYPE_MAP")
    assert not hasattr(_manifest_overlay, "_TYPE_MAP")


def test_message_matches_platform_wording():
    assert invalid_property_type_message("number", "request_data.x") == (
        f"Invalid property type 'number' at request_data.x. {PERMITTED_TEXT}"
    )
    assert invalid_property_type_message("int") == f"Invalid property type 'int'. {PERMITTED_TEXT}"


@pytest.mark.parametrize("value", PERMITTED_PROPERTY_TYPE_ORDER)
def test_canonical_types_are_permitted(value):
    assert is_permitted_property_type(value)


@pytest.mark.parametrize("value", LEGACY_SPELLINGS)
def test_legacy_spellings_are_not_permitted(value):
    assert not is_permitted_property_type(value)


@pytest.mark.parametrize(
    ("value", "permitted"),
    [
        (["string", "null"], True),
        (["integer", "float"], True),
        (["null"], False),
        ([], False),
        (["number", "null"], False),
        ([7], False),
        (7, False),
        ({"type": "string"}, False),
    ],
)
def test_list_types(value, permitted):
    assert is_permitted_property_type(value) is permitted


# ── Edge comparison: no aliasing ─────────────────────────────────────────


@pytest.mark.parametrize("shorthand", [True, False], ids=["shorthand", "dict"])
@pytest.mark.parametrize("value", PERMITTED_PROPERTY_TYPE_ORDER)
def test_same_canonical_type_matches(value, shorthand):
    assert _errors(value, value, shorthand=shorthand) == []


@pytest.mark.parametrize("shorthand", [True, False], ids=["shorthand", "dict"])
@pytest.mark.parametrize(
    ("provider", "consumer"),
    [
        # Two different types, never aliased onto one another.
        ("integer", "int"),
        ("number", "float"),
        ("bool", "boolean"),
        ("str", "string"),
        ("Integer", "integer"),
        # Genuinely different.
        ("integer", "string"),
        ("boolean", "integer"),
        ("integer", "float"),
    ],
)
def test_different_spellings_mismatch(provider, consumer, shorthand):
    errors = _errors(provider, consumer, shorthand=shorthand)
    assert len(errors) == 1
    assert "type mismatch" in errors[0]


def test_mismatch_message_names_types_as_declared():
    errors = _errors("int", "str", shorthand=True)
    assert "has 'int'" in errors[0]
    assert "expects 'str'" in errors[0]


def test_shorthand_compares_by_type_token():
    assert _errors("integer - a count", "integer (optional)", shorthand=True) == []
    assert canonical_type("float - a real") == "float"


def test_non_string_types_compare_as_given():
    assert _errors(["string", "null"], ["string", "null"], shorthand=False) == []
    errors = _errors(["string", "null"], ["string"], shorthand=False)
    assert len(errors) == 1
    assert "type mismatch" in errors[0]


@pytest.mark.parametrize("value", [None, 7, True, ["Int"], {"type": "int"}])
def test_canonical_type_passes_non_strings_through(value):
    assert canonical_type(value) is value


@pytest.mark.parametrize("value", [None, 7, ["int"]])
def test_non_string_type_never_raises(value):
    _errors(value, "integer", shorthand=False)
    _errors("integer", value, shorthand=False)


@pytest.mark.parametrize("empty", ["", None])
def test_missing_or_empty_type_is_exempt(empty):
    assert _errors(empty, "integer", shorthand=False) == []
    assert _errors("integer", empty, shorthand=False) == []


def test_artifact_schema_comparison_stays_raw():
    def errors(source_type: Any, target_type: Any) -> list[str]:
        return _json_artifact_schema_errors(
            {"x": {"type": source_type}}, {"x": {"type": target_type}}
        )

    assert errors("integer", "integer") == []
    assert len(errors("int", "integer")) == 1
    assert len(errors("Integer", "integer")) == 1


# ── Stored-shape walk ─────────────────────────────────────────────────────


def test_walk_covers_field_map_list_and_envelope():
    assert invalid_schema_types({"a": {"type": "number"}, "b": "int - n", "c": "string"}) == [
        ("a.type", "number"),
        ("b", "int"),
    ]
    assert invalid_schema_types([{"name": "a", "type": "double"}, {"name": "b"}], "rd") == [
        ("rd[0].type", "double")
    ]
    envelope = {
        "type": "object",
        "properties": {
            "a": {"type": "array", "items": {"type": "number"}},
            "b": {"anyOf": [{"type": "string"}, {"type": "bool"}]},
            "c": {"type": "object", "additionalProperties": {"type": "int"}},
            "d": {"type": "array", "items": [{"type": "str"}]},
        },
    }
    assert invalid_schema_types(envelope, "contract") == [
        ("contract.properties.a.items.type", "number"),
        ("contract.properties.b.anyOf[1].type", "bool"),
        ("contract.properties.c.additionalProperties.type", "int"),
        ("contract.properties.d.items[0].type", "str"),
    ]


@pytest.mark.parametrize("schema", [None, "string", 7, {}, [], ["x"], {"a": 1}])
def test_walk_ignores_shapes_without_types(schema):
    assert invalid_schema_types(schema) == []


def test_walk_allows_absent_type():
    assert invalid_schema_types({"a": {"type": None}, "b": {"description": "x"}}) == []


def test_port_schema_keys():
    port = {
        "request_data_schema": {"a": {"type": "number"}},
        "contract": {"type": "object", "properties": {"a": {"type": "number"}}},
        "artifact_output_schema": {"z": {"type": "float"}},
        "response_data_schema": None,
    }
    assert invalid_port_schema_types(port, "p") == [
        ("p.request_data_schema.a.type", "number"),
        ("p.contract.properties.a.type", "number"),
    ]
    assert invalid_port_schema_types("junk") == []


def test_declaration_schema_type_only():
    config = {
        "input_parameters": {
            "declarations": [
                {"name": "a", "type": "number", "_schema_type": "float"},
                {"name": "b", "type": "number", "_schema_type": "number"},
                {"name": "c", "type": "number"},
                "junk",
            ]
        },
        "output_parameters": {"declarations": "junk"},
    }
    assert invalid_declaration_schema_types(config, "n") == [
        ("n.input_parameters.declarations[1]._schema_type", "number")
    ]
    assert invalid_declaration_schema_types(None) == []


# ── Manifest authoring shapes ────────────────────────────────────────────


def test_manifest_data_forms():
    data = {
        "a": "number",
        "b": "int (optional) - a count",
        "c": {"type": "double"},
        "d": {"type": " float "},
        "e": {"description": "defaults to string"},
        "f": 7,
    }
    assert invalid_manifest_data_types(data, "rd") == [
        ("rd.a", "number"),
        ("rd.b", "int"),
        ("rd.c", "double"),
    ]
    listed = [{"name": "x", "type": "bool"}, {"name": "y"}, {"type": "str"}, "junk"]
    assert invalid_manifest_data_types(listed) == [("x", "bool")]
    assert invalid_manifest_data_types("junk") == []


def test_manifest_artifact_schema_forms():
    schema = {"a": "number", "b": {"type": "list"}, "c": "array", "d": None}
    assert invalid_manifest_schema_types(schema, "s") == [("s.a", "number"), ("s.b", "list")]
    assert invalid_manifest_schema_types(["junk"]) == []


_MANIFEST = """\
extension_type: TOOL
input_ports:
  - port_name: in
    request_data:
      count: {spelling}
"""


@pytest.mark.parametrize("spelling", LEGACY_SPELLINGS)
def test_manifest_legacy_spelling_is_an_error(spelling):
    _parsed, checks = parse_manifest(_MANIFEST.format(spelling=spelling), "TOOL")
    errors = [c for c in checks if c.id.startswith("MAN-PROPTYPE")]
    assert len(errors) == 1
    assert errors[0].severity == "error"
    assert errors[0].message == (
        f"Invalid property type {spelling!r} at input_ports.in.request_data.count. {PERMITTED_TEXT}"
    )
    # The suggestion is a literal (so the support catalog renders it); it must
    # still name exactly the permitted vocabulary.
    assert f"one of: {permitted_types_text()}." in (errors[0].suggestion or "")


@pytest.mark.parametrize("spelling", PERMITTED_PROPERTY_TYPE_ORDER)
def test_manifest_canonical_spelling_passes(spelling):
    _parsed, checks = parse_manifest(_MANIFEST.format(spelling=spelling), "TOOL")
    assert [c for c in checks if c.id.startswith("MAN-PROPTYPE")] == []


def test_manifest_every_port_block_is_checked():
    manifest = textwrap.dedent(
        """\
        extension_type: AGENT
        input_ports:
          - request_data: {a: number}
            artifact_input_schema: {b: double}
        tool_ports:
          - port_name: t
            response_data:
              - {name: c, type: bool}
            artifact_output_schema: {d: {type: str}}
        external_out_port:
          port_name: x
          response_data: {e: int}
        """
    )
    _parsed, checks = parse_manifest(manifest, "AGENT")
    messages = [c.message for c in checks if c.id.startswith("MAN-PROPTYPE")]
    assert [m.split(". Permitted")[0] for m in messages] == [
        "Invalid property type 'number' at input_ports[0].request_data.a",
        "Invalid property type 'double' at input_ports[0].artifact_input_schema.b",
        "Invalid property type 'bool' at tool_ports.t.response_data.c",
        "Invalid property type 'str' at tool_ports.t.artifact_output_schema.d",
        "Invalid property type 'int' at external_out_port.x.response_data.e",
    ]


# ── The manifest overlay carries spellings as authored ───────────────────


def test_overlay_never_rewrites_a_type():
    fields = _manifest_overlay.parse_data_fields({"a": "number", "b": {"type": " int "}})
    assert fields["a"]["type"] == "number"
    assert fields["b"]["type"] == "int"


# ── The graph rejects a non-canonical spelling on a port or declaration ─────────


def _graph(port_extra: dict[str, Any], config: dict[str, Any] | None = None) -> list[str]:
    nodes: list[dict[str, Any]] = [
        {"id": "s", "component_type": "START", "config": config or {}},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "so", "node_id": "s", "direction": "OUT", "port_type": "out", **port_extra},
        {"id": "ei", "node_id": "e", "direction": "IN", "port_type": "in"},
    ]
    edges = [{"source_port_id": "so", "target_port_id": "ei", "kind": "MAINFLOW"}]
    report = validate_graph(nodes=nodes, edges=edges, ports=ports)
    return [c.message for c in report.checks if c.id.startswith("ORCH-PROPTYPE")]


def test_graph_port_legacy_spelling_is_an_error():
    messages = _graph({"response_data_schema": {"x": {"type": "number"}}})
    assert messages == [
        f"Invalid property type 'number' at port 'so'.response_data_schema.x.type. {PERMITTED_TEXT}"
    ]


def test_graph_contract_envelope_is_walked():
    contract = {"type": "object", "properties": {"x": {"type": "double"}}}
    assert len(_graph({"post_tool_contract": contract})) == 1


def test_graph_declaration_schema_type_is_judged_widget_type_is_not():
    widget_only = {"input_parameters": {"declarations": [{"name": "x", "type": "number"}]}}
    assert _graph({}, widget_only) == []
    legacy = {
        "input_parameters": {
            "declarations": [{"name": "x", "type": "number", "_schema_type": "number"}]
        }
    }
    assert _graph({}, legacy) == [
        "Invalid property type 'number' at node 's'.input_parameters.declarations[0]._schema_type. "
        + PERMITTED_TEXT
    ]


def test_graph_canonical_spelling_passes():
    assert _graph({"response_data_schema": {"x": {"type": "float"}}}) == []


def test_project_manifest_legacy_spelling_reaches_the_port_and_fails(tmp_path: Path):
    # The overlay copies the manifest's spelling onto the graph port as authored;
    # the graph check then reports it, even with script validation off.
    (tmp_path / "orchestration.yaml").write_text(
        textwrap.dedent(
            """\
            nodes:
              - {id: s, component_type: START}
              - {id: t, component_type: TOOL, name: tool, script: tool.py, manifest: tool.yaml}
            edges:
              - {source_port_id: so, target_port_id: ti, kind: MAINFLOW}
            ports:
              - {id: so, node_id: s, key: out, direction: OUT, port_type: out}
              - {id: ti, node_id: t, key: in, direction: IN, port_type: in}
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "tool.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tool.yaml").write_text(
        "extension_type: TOOL\n"
        "input_ports:\n"
        "  - port_name: in\n"
        "    request_data:\n"
        "      n: number\n",
        encoding="utf-8",
    )
    report = validate_orchestration(tmp_path, validate_scripts=False)
    messages = [c.message for c in report.checks if c.id.startswith("ORCH-PROPTYPE")]
    assert messages[0].startswith(
        "Invalid property type 'number' at port 'ti'.request_data_schema.n"
    )
    assert not report.valid


# ── Widget vocabulary (nested ports): a different source, not aliases ────


def test_widget_map_is_pinned():
    # Mirrors the platform's _declaration_contracts.TYPE_MAP; change both or neither.
    assert _nested_ports._TYPE_MAP == {
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
    assert set(_nested_ports._TYPE_MAP.values()) <= PERMITTED_PROPERTY_TYPES


@pytest.mark.parametrize(
    ("decl", "expected"),
    [
        ({"type": "number"}, "float"),
        ({"type": "Number"}, "float"),
        ({"type": "number", "_schema_type": "integer"}, "integer"),
        ({"type": "number", "_schema_type": "number"}, "float"),
        ({"type": "select"}, "string"),
        ({}, "string"),
        ({"type": None}, "string"),
        ({"type": "int"}, "int"),
        ({"type": "str"}, "str"),
    ],
)
def test_declaration_property_type(decl, expected):
    assert _nested_ports.declaration_property_type(decl, "string") == expected
