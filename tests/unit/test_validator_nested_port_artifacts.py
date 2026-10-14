"""A nested node's port carries every field the platform generates, not two of them.

The platform builds a child orchestration's one ``orchestration`` port from the
child's START and END. Besides the request
and response halves, those statements stamp the artifact halves -- the child
START OUT port's output artifact becomes the port's input artifact, the child END
IN port's input artifact its output artifact -- and the port, a new row, declares
no passthrough, dynamic contract or legacy artifact constraint.

The SDK stamps them too: a port carrying only the request and response
halves gives a parent edge into a nested node nothing to compare its artifact
type against, so ``perceivo validate`` would pass a bundle the platform refuses
on import with ``Port artifact type mismatch``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from perceivo._bundle.orchestration import bundle_to_orchestration
from perceivo.validator._orchestration import validate_orchestration
from perceivo.validator._orchestration._nested_ports import (
    apply_nested_port_fields,
    child_port_meta,
    contract_to_fields,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "orchestration_company_policy_chatbot_rag_nested.json"
)
# The root START component, and the nested Shield Executor its MAINFLOW edge feeds.
_ROOT_START = "comp_2"
_MISMATCH = (
    "Port artifact type mismatch: source produces 'optional(text)', target expects 'optional(json)'"
)

_X_CONTRACT = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
_X_FIELDS = {"x": {"type": "string", "required": True}}
_K_SCHEMA = {"k": {"type": "string"}}
_Q_DECL = {"input_parameters": {"declarations": [{"name": "q", "type": "text", "required": True}]}}
_R_DECL = {"output_parameters": {"declarations": [{"name": "r", "type": "integer"}]}}
_CLEARED = {
    "artifact_type_constraint": None,
    "response_data_passthrough": None,
    "response_data_passthrough_except": None,
    "response_data_contract": None,
}

# Each row: (START config, START OUT port, END config, END IN port, the port the
# platform generates). The last column is what the platform generates from the
# first four; the request and response halves are platform JSON Schema
# contracts, which the SDK holds as flat fields.
_PLATFORM_CASES: dict[str, tuple[Any, ...]] = {
    "ports_only": (
        {},
        {
            "contract": _X_CONTRACT,
            "artifact_type_output": "optional(json)",
            "artifact_output_schema": _K_SCHEMA,
        },
        {},
        {"contract": _X_CONTRACT, "artifact_type_input": "json", "artifact_input_schema": None},
        {
            "request_data_schema": _X_CONTRACT,
            "response_data_schema": _X_CONTRACT,
            "artifact_type_input": "optional(json)",
            "artifact_input_schema": _K_SCHEMA,
            "artifact_type_output": "json",
            "artifact_output_schema": None,
        },
    ),
    "declarations_still_take_the_port_artifacts": (
        _Q_DECL,
        {
            "contract": _X_CONTRACT,
            "response_data_schema": _X_FIELDS,
            "artifact_type_output": "optional(json)",
            "artifact_output_schema": _K_SCHEMA,
        },
        _R_DECL,
        {
            "contract": _X_CONTRACT,
            "request_data_schema": _X_FIELDS,
            "artifact_type_input": "none",
            "artifact_input_schema": None,
        },
        {
            "request_data_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            "response_data_schema": {"type": "object", "properties": {"r": {"type": "integer"}}},
            "artifact_type_input": "optional(json)",
            "artifact_input_schema": _K_SCHEMA,
            "artifact_type_output": "none",
            "artifact_output_schema": None,
        },
    ),
    "empty_artifact_type_keeps_its_schema": (
        {},
        {
            "contract": _X_CONTRACT,
            "response_data_schema": _X_FIELDS,
            "artifact_type_output": "",
            "artifact_output_schema": {"a": {"type": "string"}},
        },
        {},
        {"contract": _X_CONTRACT, "request_data_schema": _X_FIELDS},
        {
            "request_data_schema": _X_FIELDS,
            "response_data_schema": _X_CONTRACT,
            "artifact_type_input": "",
            "artifact_input_schema": {"a": {"type": "string"}},
            "artifact_type_output": None,
            "artifact_output_schema": None,
        },
    ),
    "no_start_out_port": (
        _Q_DECL,
        None,
        {},
        {"artifact_type_input": "optional(json)", "artifact_input_schema": _K_SCHEMA},
        {
            "request_data_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            "response_data_schema": None,
            "artifact_type_input": None,
            "artifact_input_schema": None,
            "artifact_type_output": "optional(json)",
            "artifact_output_schema": _K_SCHEMA,
        },
    ),
    "unnamed_declarations_fall_back_to_the_port": (
        {"input_parameters": {"declarations": [{"type": "text"}]}},
        {"contract": _X_CONTRACT, "artifact_type_output": "json", "artifact_output_schema": None},
        _R_DECL,
        None,
        {
            "request_data_schema": _X_CONTRACT,
            "response_data_schema": {"type": "object", "properties": {"r": {"type": "integer"}}},
            "artifact_type_input": "json",
            "artifact_input_schema": None,
            "artifact_type_output": None,
            "artifact_output_schema": None,
        },
    ),
}


def _loaded(start_config, start_out, end_config, end_in) -> dict[str, Any]:
    ports = []
    if start_out is not None:
        ports.append({"id": "so", "node_id": "s", "direction": "out", **start_out})
    if end_in is not None:
        ports.append({"id": "ei", "node_id": "e", "direction": "in", **end_in})
    return {
        "nodes": [
            {"id": "s", "component_type": "START", "config": start_config},
            {"id": "e", "component_type": "END", "config": end_config},
        ],
        "ports": ports,
        "component_types": {},
    }


@pytest.mark.parametrize("case", sorted(_PLATFORM_CASES))
def test_generated_port_matches_platform_field_by_field(case: str) -> None:
    start_config, start_out, end_config, end_in, platform = copy.deepcopy(_PLATFORM_CASES[case])
    expected = {**platform, **_CLEARED}
    for half in ("request_data_schema", "response_data_schema"):
        expected[half] = contract_to_fields(expected[half])
    assert child_port_meta(_loaded(start_config, start_out, end_config, end_in)) == expected


def test_the_parent_copy_of_every_generated_field_is_replaced(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    loaded = _loaded({}, {"artifact_type_output": "optional(json)"}, {}, {})
    ports = [
        {
            "id": "n_in",
            "node_id": "n",
            "direction": "in",
            "artifact_type_input": "text",
            "artifact_type_constraint": "text",
            "response_data_passthrough": "request_data",
            "response_data_passthrough_except": ["x"],
            "response_data_contract": "dynamic",
        }
    ]
    nodes = [{"id": "n", "component_type": "ORCHESTRATION", "project": "child"}]
    apply_nested_port_fields(
        str(tmp_path), nodes, ports, lambda _id: "ORCHESTRATION", lambda _dir: loaded
    )
    assert ports == [
        {"id": "n_in", "node_id": "n", "direction": "in", "artifact_type_input": "optional(json)"}
    ]


# -- the Company Policy Chatbot RAG bundle, which the platform imports ---------


def _validate(bundle: dict[str, Any], tmp_path: Path):
    return validate_orchestration(str(bundle_to_orchestration(bundle, tmp_path / "project")))


def _with_root_start_output(bundle: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    """Only the root START's output artifact type changes."""
    bundle = copy.deepcopy(bundle)
    start = bundle["components"][_ROOT_START]
    assert start["category"] == "START"
    before = "artifact_type_output: optional(json)"
    assert start["manifest_yaml"].count(before) == 1
    start["manifest_yaml"] = start["manifest_yaml"].replace(
        before, f"artifact_type_output: {artifact_type}"
    )
    (out_port,) = [p for p in start["ports"] if p["direction"] == "OUT"]
    out_port["artifact_type_output"] = artifact_type
    return bundle


@pytest.fixture(scope="module")
def rag_bundle() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def test_the_bundle_the_platform_accepts_still_validates(
    rag_bundle: dict[str, Any], tmp_path: Path
) -> None:
    report = _validate(rag_bundle, tmp_path)
    assert report.valid, [(c.id, c.message) for c in report.errors]


def test_a_start_artifact_the_nested_child_cannot_take_is_rejected(
    rag_bundle: dict[str, Any], tmp_path: Path
) -> None:
    report = _validate(_with_root_start_output(rag_bundle, "optional(text)"), tmp_path)
    assert not report.valid
    assert [(c.title, c.message) for c in report.errors] == [("Artifact type mismatch", _MISMATCH)]
