"""Coverage-focused unit tests for the validator's contract, manifest and
security checks.

Targets:
- perceivo.validator._contracts
- perceivo.validator._manifest -- ``parse_manifest`` only
- perceivo.validator._security

These validators are pure: they take dicts / source strings / ASTs and return
lists of error strings or ValidationReport / ValidationCheck objects.

Port-rule and component checks have their own files,
``test_cov_validator_port_rules.py`` and ``test_cov_validator_component.py``,
which keeps each file under the 500-line cap.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from perceivo.validator import _manifest as _manifest_module
from perceivo.validator._contracts import (
    _extract_fields,
    validate_port_contract_match,
)
from perceivo.validator._manifest import parse_manifest
from perceivo.validator._security import (
    _get_call_name,
    _is_blocked_module,
    run_security_checks,
)

# ===========================================================================
# contracts.validate_port_contract_match
# ===========================================================================


def test_contract_invoke_edge_validated():
    # INVOKE edges are validated bidirectionally: an incompatible pair
    # must produce errors, not pass silently.
    src = {"response_data_schema": {"a": "string"}}
    tgt = {"request_data_schema": {"b": {"type": "int"}}}
    errors = validate_port_contract_match(src, tgt, edge_kind="INVOKE")
    # forward: target requires 'b' (source request declares nothing);
    # reverse: source requires 'a' (target response declares nothing).
    assert any("Required field 'b'" in e for e in errors)
    assert any("Required field 'a'" in e for e in errors)


def test_contract_invoke_edge_compatible_passes():
    # A fully-covered INVOKE pair (forward request + reverse response) is valid.
    src = {
        "request_data_schema": {"b": {"type": "int"}},
        "response_data_schema": {},
    }
    tgt = {
        "request_data_schema": {"b": {"type": "int"}},
        "response_data_schema": {},
    }
    assert validate_port_contract_match(src, tgt, edge_kind="INVOKE") == []


def test_contract_start_source_mainflow_validated():
    # No START exemption: a START whose OUT declares nothing while
    # the target requires a field is an error.
    src = {"response_data_schema": {}}
    tgt = {"request_data_schema": {"b": {"type": "int"}}}
    errors = validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")
    assert any("Required field 'b'" in e for e in errors)


def test_contract_takes_no_source_component_type():
    # The contract rule is the same for every source type, so it takes none.
    parameters = inspect.signature(validate_port_contract_match).parameters
    assert list(parameters) == ["source_port", "target_port", "edge_kind"]


def test_manifest_module_holds_no_port_rule_or_table_names():
    # Each name lives only in the module that defines it.
    names = (
        "_manifest_ports",
        "validate_port_rules",
        "COMPONENT_REQUIRED_METHODS",
        "COMPONENT_BASES",
        "_BASE_HOMES",
        "DECORATOR_NAMES",
    )
    assert [name for name in names if hasattr(_manifest_module, name)] == []


def test_contract_empty_source_schema_errors_when_required():
    # An empty provider is not an escape hatch: a required consumer
    # field with no provider field is an error.
    src = {"response_data_schema": {}}
    tgt = {"request_data_schema": {"b": {"type": "int"}}}
    errors = validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")
    assert any("Required field 'b'" in e for e in errors)


def test_contract_empty_source_schema_ok_when_consumer_optional():
    # Empty provider is fine when the consumer requires nothing.
    src = {"response_data_schema": {}}
    tgt = {"request_data_schema": {"b": {"type": "int", "required": False}}}  # b not required
    assert validate_port_contract_match(src, tgt, edge_kind="MAINFLOW") == []


def test_contract_empty_target_schema_skips():
    src = {"response_data_schema": {"a": "string"}}
    tgt = {"request_data_schema": {}}
    assert validate_port_contract_match(src, tgt, edge_kind="MAINFLOW") == []


def test_contract_missing_required_field_errors():
    src = {"response_data_schema": {"a": {"type": "string"}}}
    tgt = {"request_data_schema": {"b": {"type": "string"}}}
    errors = validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")
    assert len(errors) == 1
    assert "Required field 'b'" in errors[0]


def test_contract_missing_optional_field_no_error():
    src = {"response_data_schema": {"a": {"type": "string"}}}
    tgt = {"request_data_schema": {"b": {"type": "string", "required": False}}}  # b not required
    assert validate_port_contract_match(src, tgt, edge_kind="MAINFLOW") == []


def test_contract_type_mismatch_errors():
    src = {"response_data_schema": {"a": {"type": "string"}}}
    tgt = {"request_data_schema": {"a": {"type": "int"}}}
    errors = validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")
    assert len(errors) == 1
    assert "type mismatch" in errors[0]


def test_contract_type_match_no_error():
    src = {"response_data_schema": {"a": {"type": "string"}}}
    tgt = {"request_data_schema": {"a": {"type": "string"}}}
    assert validate_port_contract_match(src, tgt, edge_kind="MAINFLOW") == []


def test_contract_missing_type_info_skips_mismatch():
    # Source field present but with no type -> mismatch branch skipped.
    src = {"response_data_schema": {"a": {}}}
    tgt = {"request_data_schema": {"a": {"type": "int"}}}
    assert validate_port_contract_match(src, tgt, edge_kind="MAINFLOW") == []


def test_contract_default_edge_kind_runs_validation():
    # Default (empty) edge_kind is not INVOKE and source type not START.
    src = {"response_data_schema": {"a": {"type": "string"}}}
    tgt = {"request_data_schema": {"x": {"type": "string"}}}
    errors = validate_port_contract_match(src, tgt)
    assert any("Required field 'x'" in e for e in errors)


# ===========================================================================
# contracts._extract_fields
# ===========================================================================


def test_extract_fields_flat_dict_and_required_default():
    schema = {"a": {"type": "string"}}
    fields = _extract_fields(schema)
    assert fields == {"a": {"type": "string", "required": True}}


def test_extract_fields_does_not_unwrap_json_schema_envelope():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    fields = _extract_fields(schema)
    assert "a" not in fields
    assert fields["properties"] == {"type": "", "required": True}


def test_extract_fields_non_mapping_returns_empty_instead_of_crashing():
    assert _extract_fields([{"a": "string"}]) == {}


def test_contract_accepts_flat_field_named_properties():
    schema = {"properties": {"type": "string"}}
    src = {"response_data_schema": schema}
    tgt = {"request_data_schema": schema}

    assert validate_port_contract_match(src, tgt, edge_kind="MAINFLOW") == []
    assert _extract_fields(schema) == {"properties": {"type": "string", "required": True}}


def test_contract_rejects_json_schema_envelope_cleanly():
    src = {"response_data_schema": {"a": {"type": "string"}}}
    tgt = {
        "request_data_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
    }
    errors = validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")
    assert errors == [
        "Invalid target request_data_schema: JSON Schema properties envelopes "
        "are not supported; declare fields directly."
    ]


def test_contract_reports_non_mapping_schema_instead_of_crashing():
    src = {"response_data_schema": ["not", "a", "mapping"]}
    tgt = {"request_data_schema": {"a": {"type": "string"}}}
    errors = validate_port_contract_match(src, tgt, edge_kind="MAINFLOW")
    assert errors == [
        "Invalid source response_data_schema: schema must be a field mapping, found list."
    ]


def test_extract_fields_simple_str_format():
    # Flat form is required by default, matching the platform validator.
    fields = _extract_fields({"a": "string"})
    assert fields == {"a": {"type": "string", "required": True}}


def test_extract_fields_simple_dict_required_defaults_true():
    # A flat dict field with no explicit 'required' key defaults to required=True.
    fields = _extract_fields({"a": {"type": "int"}})
    assert fields == {"a": {"type": "int", "required": True}}


def test_extract_fields_simple_dict_explicit_optional():
    # Only an explicit required=False opts a flat field out of being required.
    fields = _extract_fields({"a": {"type": "int", "required": False}})
    assert fields == {"a": {"type": "int", "required": False}}


def test_extract_fields_simple_dict_format():
    fields = _extract_fields({"a": {"type": "int", "required": True}})
    assert fields == {"a": {"type": "int", "required": True}}


def test_extract_fields_simple_keeps_other_types_as_untyped_required():
    assert _extract_fields({"a": 123}) == {"a": {"type": "", "required": True}}


# ===========================================================================
# manifest.parse_manifest
# ===========================================================================


def test_parse_manifest_empty_string():
    parsed, checks = parse_manifest("", "AGENT")
    assert parsed is None
    assert any(c.id.startswith("MAN-EMPTY") for c in checks)


def test_parse_manifest_whitespace_only():
    parsed, checks = parse_manifest("   \n  ", "AGENT")
    assert parsed is None
    assert any(c.id.startswith("MAN-EMPTY") for c in checks)


def test_parse_manifest_none():
    parsed, checks = parse_manifest(None, "AGENT")
    assert parsed is None
    assert any(c.id.startswith("MAN-EMPTY") for c in checks)


def test_parse_manifest_invalid_yaml():
    parsed, checks = parse_manifest("key: [unclosed\n", "AGENT")
    assert parsed is None
    assert any(c.id.startswith("MAN-PARSE") for c in checks)


def test_parse_manifest_not_a_mapping():
    parsed, checks = parse_manifest("- item1\n- item2\n", "AGENT")
    assert parsed is None
    assert any(c.id.startswith("MAN-FORMAT") for c in checks)


def test_parse_manifest_scalar_not_mapping():
    parsed, checks = parse_manifest("just_a_string", "AGENT")
    assert parsed is None
    assert any(c.id.startswith("MAN-FORMAT") for c in checks)


def test_parse_manifest_type_mismatch():
    parsed, checks = parse_manifest("extension_type: TOOL\n", "AGENT")
    assert parsed is not None
    assert any(c.id.startswith("MAN-TYPE") for c in checks)


def test_parse_manifest_layer_is_not_fsm():
    # LAYER is not an alias of FSM: the manifest type and the component type differ.
    parsed, checks = parse_manifest("extension_type: LAYER\n", "FSM")
    assert parsed is not None
    assert any(c.id.startswith("MAN-TYPE") for c in checks)


def test_parse_manifest_no_ext_type_skips_type_check():
    parsed, checks = parse_manifest("name: foo\n", "AGENT")
    assert parsed is not None
    assert not any(c.id.startswith("MAN-TYPE") for c in checks)


def test_parse_manifest_matching_type_no_error():
    parsed, checks = parse_manifest("extension_type: AGENT\n", "AGENT")
    assert parsed is not None
    assert not any(c.id.startswith("MAN-TYPE") for c in checks)


def test_parse_manifest_flat_ports_list_is_an_error():
    manifest = textwrap.dedent(
        """
        extension_type: AGENT
        ports:
          - base_schema: AgentIn
        """
    )
    parsed, checks = parse_manifest(manifest, "AGENT")
    assert parsed is not None
    assert [c.id for c in checks] == ["MAN-PORTS-001"]
    assert not any(c.id.startswith("MAN-SCHEMA") for c in checks)


def test_parse_manifest_ports_not_a_list_skipped():
    manifest = textwrap.dedent(
        """
        extension_type: AGENT
        ports: not_a_list
        """
    )
    parsed, checks = parse_manifest(manifest, "AGENT")
    assert parsed is not None
    assert not any(c.id.startswith("MAN-SCHEMA") for c in checks)


def test_parse_manifest_port_without_base_schema_ok():
    manifest = textwrap.dedent(
        """
        extension_type: AGENT
        ports:
          - direction: IN
        """
    )
    parsed, checks = parse_manifest(manifest, "AGENT")
    assert parsed is not None
    assert not any(c.id.startswith("MAN-SCHEMA") for c in checks)


# ===========================================================================
# security — remaining branches
# ===========================================================================


def test_security_blocked_root_import_subprocess():
    checks = run_security_checks(ast.parse("import subprocess\n"))
    assert any(c.title == "Blocked import" and c.severity == "error" for c in checks)


def test_security_blocked_app_import_from_root():
    checks = run_security_checks(ast.parse("from app.routes import x\n"))
    assert any(c.title == "Blocked import" and c.severity == "error" for c in checks)


def test_security_redis_import_warning():
    checks = run_security_checks(ast.parse("import redis\n"))
    redis = [c for c in checks if c.id.startswith("SEC-REDIS")]
    assert redis
    assert all(c.severity == "warning" for c in redis)


def test_security_blocked_eval_call():
    checks = run_security_checks(ast.parse("y = eval('1+1')\n"))
    assert any(c.id.startswith("SEC-CALL") and "eval" in c.message for c in checks)


def test_security_blocked_process_call():
    checks = run_security_checks(ast.parse("import subprocess\nsubprocess.run(['ls'])\n"))
    assert any(c.id.startswith("SEC-PROCESS") for c in checks)


def test_security_external_url_warning():
    checks = run_security_checks(ast.parse('U = "https://example.com/x"\n'))
    urls = [c for c in checks if c.id.startswith("SEC-URL")]
    assert urls
    assert all(c.severity == "warning" for c in urls)


def test_security_import_from_blocked_module_not_root():
    # importlib.util: root "importlib" is not a blocked import root, but the
    # full module is a blocked module.
    checks = run_security_checks(ast.parse("from importlib.util import spec_from_file_location\n"))
    assert any(c.title == "Blocked module" for c in checks)


def test_security_import_blocked_module_alias():
    checks = run_security_checks(ast.parse("import importlib.machinery\n"))
    assert any(c.title == "Blocked module" for c in checks)


def test_security_import_from_without_module_ignored():
    # `from . import x` has module=None -> the ImportFrom branch is skipped.
    checks = run_security_checks(ast.parse("from . import something\n"))
    assert checks == []


def test_is_blocked_module_exact_and_root():
    assert _is_blocked_module("subprocess") is True
    assert _is_blocked_module("importlib.util") is True
    assert _is_blocked_module("subprocess.something") is True
    assert _is_blocked_module("json") is False


def test_get_call_name_plain_name():
    call = ast.parse("foo()").body[0].value
    assert _get_call_name(call) == "foo"


def test_get_call_name_dotted_attribute():
    call = ast.parse("os.path.join()").body[0].value
    assert _get_call_name(call) == "os.path.join"


def test_get_call_name_attribute_on_non_name_base():
    # arr[0].method() -> base of the attribute is a Subscript, not a Name.
    call = ast.parse("arr[0].method()").body[0].value
    assert _get_call_name(call) == "method"


def test_get_call_name_non_name_non_attribute_func():
    # A call whose func is itself a Call -> neither Name nor Attribute.
    call = ast.parse("factory()()").body[0].value
    assert _get_call_name(call) == ""
