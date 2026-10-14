from __future__ import annotations

import copy
import json
import types
from pathlib import Path
from typing import Any, Literal, Union, cast, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from perceivo._bundle import schema
from perceivo.orchestration._builder._bundle_integrity import (
    BundleFidelityError,
    assert_bundle_fidelity,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "platform_orchestration_export_fields.json"
MODELS: dict[str, type[BaseModel]] = {
    name: getattr(schema, name)
    for name in (
        "PortExportData",
        "FSMStateExportData",
        "ComponentExportData",
        "OrchestrationExportMetadata",
        "NodeExportData",
        "PortRefExportData",
        "EdgeExportData",
        "SecretHintData",
        "OrchestrationExport",
    )
}


def _annotation_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (types.UnionType, Union):
        return "|".join(_annotation_name(item) for item in args)
    if origin is Literal:
        return f"Literal[{','.join(repr(item) for item in args)}]"
    if origin in (list, dict):
        if not args:
            return cast(str, origin.__name__)
        return f"{origin.__name__}[{','.join(_annotation_name(item) for item in args)}]"
    if annotation is type(None):
        return "None"
    name = getattr(annotation, "__name__", None)
    return cast(str, name) if name is not None else str(annotation).replace("typing.", "")


def _field_default(field: Any) -> dict[str, Any]:
    if field.is_required():
        return {"kind": "required"}
    if field.default_factory is not None:
        return {"factory": field.default_factory.__name__, "kind": "factory"}
    return {"kind": "value", "value": field.default}


def _normalize_platform_annotation(annotation: str) -> str:
    """Spell the platform's bare collections as their explicit-Any equivalent.

    The pinned platform model uses bare ``dict`` and ``list`` annotations. The
    strict SDK mirror spells only those exact union members as ``dict[str, Any]``
    and ``list[Any]``. Parameterized collections, scalar types, union
    nullability, and every other annotation remain byte-for-byte significant.
    """
    equivalents = {"dict": "dict[str,Any]", "list": "list[Any]"}
    return "|".join(equivalents.get(member, member) for member in annotation.split("|"))


def _assert_pin_matches(fixture: dict[str, Any]) -> None:
    assert set(fixture["models"]) == set(MODELS)
    for model_name, model in MODELS.items():
        expected = fixture["models"][model_name]["fields"]
        assert set(model.model_fields) == set(expected), model_name
        for field_name, field in model.model_fields.items():
            assert _annotation_name(field.annotation) == _normalize_platform_annotation(
                expected[field_name]["annotation"]
            )
            assert _field_default(field) == expected[field_name]["default"]


def _bundle() -> dict[str, Any]:
    return {
        "export_version": "1.0",
        "export_type": "orchestration",
        "exported_at": "2026-09-15T00:00:00Z",
        "orchestration": {
            "name": "fidelity",
            "runtime_config": {"customer_key": {"nested": True}},
        },
        "components": {
            "comp_0": {
                "name": "Reference",
                "category": "ORCHESTRATION",
                "config": {"arbitrary": {"freeform": [1, None]}},
                "ports": [
                    {
                        "direction": "OUT",
                        "key": "result",
                        "port_type": "out",
                        "response_data_passthrough": "response_data",
                        "response_data_passthrough_except": ["private"],
                        "response_data_contract": "ResultData",
                    }
                ],
            }
        },
        "nodes": [
            {
                "local_id": "node_0",
                "component_ref": "comp_0",
                "orchestration_asset_id": "00000000-0000-0000-0000-000000000001",
                "config": {"customer_node_key": "kept"},
            }
        ],
        "ports": [],
        "edges": [],
    }


def test_committed_pin_matches_the_sdk_mirror_fields_types_and_defaults():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    _assert_pin_matches(fixture)


@pytest.mark.parametrize(
    ("field_name", "attribute", "mutated"),
    [
        ("config", "annotation", "dict[str,str]|None"),
        ("config", "annotation", "dict"),
        ("requires_gpu", "default", {"kind": "value", "value": True}),
    ],
)
def test_pin_normalization_does_not_hide_parameter_nullability_or_default_drift(
    field_name: str, attribute: str, mutated: Any
) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["models"]["ComponentExportData"]["fields"][field_name][attribute] = mutated

    with pytest.raises(AssertionError):
        _assert_pin_matches(fixture)


def test_non_null_passthrough_and_private_reference_loss_reports_all_paths():
    original = _bundle()
    candidate = copy.deepcopy(original)
    port = candidate["components"]["comp_0"]["ports"][0]
    port.pop("response_data_passthrough")
    port.pop("response_data_passthrough_except")
    port.pop("response_data_contract")
    candidate["nodes"][0].pop("orchestration_asset_id")

    with pytest.raises(BundleFidelityError) as caught:
        assert_bundle_fidelity(original, candidate)

    message = str(caught.value)
    assert "components.comp_0.ports[0].response_data_passthrough:" in message
    assert "components.comp_0.ports[0].response_data_passthrough_except:" in message
    assert "components.comp_0.ports[0].response_data_contract:" in message
    assert "nodes[0].orchestration_asset_id:" in message


def test_undeclared_model_key_fails_before_defaults_can_hide_it():
    original = _bundle()
    original["components"]["comp_0"]["ports"][0]["future_platform_field"] = "non-null"
    candidate = _bundle()

    with pytest.raises(BundleFidelityError, match=r"\$original\.components\.comp_0") as caught:
        assert_bundle_fidelity(original, candidate)

    assert "future_platform_field" in str(caught.value)


def test_freeform_dictionary_keys_are_values_not_schema_fields():
    original = _bundle()
    assert_bundle_fidelity(original, copy.deepcopy(original))

    candidate = copy.deepcopy(original)
    candidate["components"]["comp_0"]["config"]["arbitrary"]["freeform"] = [2, None]
    with pytest.raises(
        BundleFidelityError, match=r"components\.comp_0\.config\.arbitrary\.freeform"
    ):
        assert_bundle_fidelity(original, candidate)


def test_declared_default_may_be_explicit_but_arbitrary_null_is_not_equal():
    original = _bundle()
    candidate = copy.deepcopy(original)
    candidate["orchestration"]["requires_gpu"] = False
    assert_bundle_fidelity(original, candidate)

    candidate["orchestration"]["name"] = None
    with pytest.raises(BundleFidelityError, match="schema validation failed"):
        assert_bundle_fidelity(original, candidate)


def test_only_explicitly_allowed_differences_are_ignored():
    original = _bundle()
    candidate = copy.deepcopy(original)
    candidate["exported_at"] = "2026-09-16T00:00:00Z"
    assert_bundle_fidelity(original, candidate)

    candidate["orchestration"]["name"] = "renamed"
    with pytest.raises(BundleFidelityError, match=r"orchestration\.name"):
        assert_bundle_fidelity(original, candidate)
    assert_bundle_fidelity(
        original,
        candidate,
        allowed_differences=("exported_at", "orchestration.name"),
    )


def test_node_rejects_embedded_and_private_references_together():
    with pytest.raises(
        ValidationError, match=r"both orchestration_ref.*orchestration_asset_id"
    ) as caught:
        schema.NodeExportData(
            local_id="node_0",
            component_ref="orch_0",
            orchestration_ref="orch_0",
            orchestration_asset_id="00000000-0000-0000-0000-000000000001",
        )
    assert isinstance(caught.value, ValueError)
    assert isinstance(caught.value.errors()[0]["ctx"]["error"], schema.NestedOrchestrationError)
