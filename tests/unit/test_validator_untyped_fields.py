"""A field whose value is neither a type string nor a spec dict is present.

The platform reads a port-contract field declared as ``None`` (YAML ``name:``
with no value), a number or a list as present, required and untyped. The SDK
must reach the same accept/reject answer: dropping the field made it reject a
provider the platform accepts and accept a consumer the platform rejects.
"""

from __future__ import annotations

from typing import Any

import pytest

from perceivo.validator._contracts import _extract_fields, validate_port_contract_match

UNTYPED_VALUES = [None, 5, 1.5, True, ["string"]]
UNTYPED_IDS = ["none", "int", "float", "bool", "list"]


def _mainflow(provider: dict[str, Any], consumer: dict[str, Any]) -> list[str]:
    return validate_port_contract_match(
        {"response_data_schema": provider},
        {"request_data_schema": consumer},
        edge_kind="MAINFLOW",
    )


def _invoke_forward(provider: dict[str, Any], consumer: dict[str, Any]) -> list[str]:
    return validate_port_contract_match(
        {"request_data_schema": provider},
        {"request_data_schema": consumer},
        edge_kind="INVOKE",
    )


def _invoke_return(provider: dict[str, Any], consumer: dict[str, Any]) -> list[str]:
    return validate_port_contract_match(
        {"response_data_schema": consumer},
        {"response_data_schema": provider},
        edge_kind="INVOKE",
    )


LEGS = [_mainflow, _invoke_forward, _invoke_return]
LEG_IDS = ["mainflow", "invoke-forward", "invoke-return"]


def test_untyped_values_are_kept_as_present_required_untyped():
    schema = dict(zip(UNTYPED_IDS, UNTYPED_VALUES, strict=True))
    assert _extract_fields(schema) == {name: {"type": "", "required": True} for name in UNTYPED_IDS}


@pytest.mark.parametrize("leg", LEGS, ids=LEG_IDS)
@pytest.mark.parametrize("value", UNTYPED_VALUES, ids=UNTYPED_IDS)
def test_untyped_provider_field_covers_a_typed_consumer_field(leg, value):
    assert leg({"x": value}, {"x": "string"}) == []


@pytest.mark.parametrize("leg", LEGS, ids=LEG_IDS)
@pytest.mark.parametrize("value", UNTYPED_VALUES, ids=UNTYPED_IDS)
def test_untyped_consumer_field_is_required(leg, value):
    errors = leg({"a": "string"}, {"x": value})
    assert len(errors) == 1
    assert "Required field 'x'" in errors[0]


@pytest.mark.parametrize("leg", LEGS, ids=LEG_IDS)
@pytest.mark.parametrize("value", UNTYPED_VALUES, ids=UNTYPED_IDS)
def test_untyped_consumer_field_is_covered_by_any_provider_field(leg, value):
    assert leg({"x": "integer"}, {"x": value}) == []


@pytest.mark.parametrize("leg", LEGS, ids=LEG_IDS)
def test_typed_fields_are_unchanged(leg):
    assert leg({"x": "string"}, {"x": {"type": "string"}}) == []
    assert len(leg({"x": "string"}, {"x": {"type": "integer"}})) == 1
    assert len(leg({"a": "string"}, {"x": "string"})) == 1


@pytest.mark.parametrize("leg", LEGS, ids=LEG_IDS)
def test_explicitly_optional_consumer_field_is_still_exempt(leg):
    assert leg({"a": "string"}, {"x": {"type": "string", "required": False}}) == []
