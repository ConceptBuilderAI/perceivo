"""A nested node's ports carry the fields the platform generates for a child.

The platform builds one ``orchestration`` port per child orchestration from the
child's START and END. The SDK's nested ports carried nothing, so:

* story-teller shape -- a router child whose END receives ``routed_to`` /
  ``match_reason`` could never provide them to the parent's END (false reject);
* classifier shape -- a child whose START declares a required ``user_prompt``
  was invokable by a router that never sends it (false accept).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from perceivo.validator._orchestration import validate_orchestration
from perceivo.validator._orchestration._nested_ports import (
    child_port_fields,
    contract_to_fields,
    declarations_to_contract,
)

_STORY_FIELDS = {
    "routed_to": {"type": "string", "description": "Branch the router chose"},
    "match_reason": {"type": "string", "description": "Why it matched"},
}
_STORY_CONTRACT = {
    "type": "object",
    "properties": dict(_STORY_FIELDS),
    "required": ["routed_to", "match_reason"],
}


def _write(path: Path, graph: dict[str, Any]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    return path


def _child(
    path: Path,
    *,
    start_config: dict | None = None,
    start_out: dict | None = None,
    end_config: dict | None = None,
    end_in: dict | None = None,
    agent_returns: dict | None = None,
) -> Path:
    """START -> AGENT -> END, with the START OUT / END IN ports given."""
    nodes = [
        {"id": "s", "name": "s", "component_type": "START", "config": start_config or {}},
        {"id": "a", "name": "a", "component_type": "AGENT"},
        {"id": "e", "name": "e", "component_type": "END", "config": end_config or {}},
    ]
    ports = [
        {
            "id": "s_out",
            "node_id": "s",
            "name": "output",
            "port_type": "out",
            "direction": "out",
            **(start_out or {}),
        },
        {"id": "a_in", "node_id": "a", "name": "input", "port_type": "in", "direction": "in"},
        {
            "id": "a_out",
            "node_id": "a",
            "name": "output",
            "port_type": "out",
            "direction": "out",
            "response_data_schema": agent_returns or {},
        },
        {
            "id": "e_in",
            "node_id": "e",
            "name": "input",
            "port_type": "in",
            "direction": "in",
            **(end_in or {}),
        },
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"},
        {"source_port_id": "a_out", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    return _write(path, {"nodes": nodes, "ports": ports, "edges": edges})


def _story_parent(root: Path, end_in: dict) -> Path:
    """START -> nested router child -> END requiring the router's fields."""
    _child(root / "router", end_in=end_in, agent_returns=dict(_STORY_FIELDS))
    nodes = [
        {"id": "s", "name": "s", "component_type": "START"},
        {"id": "n", "name": "router", "component_type": "ORCHESTRATION", "project": "router"},
        {"id": "e", "name": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_out", "node_id": "s", "name": "output", "port_type": "out", "direction": "out"},
        {"id": "n_in", "node_id": "n", "name": "input", "port_type": "in", "direction": "in"},
        {"id": "n_out", "node_id": "n", "name": "output", "port_type": "out", "direction": "out"},
        {
            "id": "e_in",
            "node_id": "e",
            "name": "input",
            "port_type": "in",
            "direction": "in",
            "request_data_schema": dict(_STORY_FIELDS),
        },
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "n_in", "kind": "MAINFLOW"},
        {"source_port_id": "n_out", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    return _write(root, {"nodes": nodes, "ports": ports, "edges": edges})


def _classifier_parent(root: Path, decls: list[dict], sends: dict) -> Path:
    """An FSM router INVOKEs a member child over a state port; the member's START declares inputs."""
    _child(root / "member", start_config={"input_parameters": {"declarations": decls}})
    nodes = [
        {"id": "s", "name": "s", "component_type": "START"},
        {"id": "r", "name": "router", "component_type": "FSM"},
        {"id": "m", "name": "member", "component_type": "ORCHESTRATION", "project": "member"},
        {"id": "e", "name": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_out", "node_id": "s", "name": "output", "port_type": "out", "direction": "out"},
        {"id": "r_in", "node_id": "r", "name": "input", "port_type": "in", "direction": "in"},
        {"id": "r_out", "node_id": "r", "name": "output", "port_type": "out", "direction": "out"},
        {
            "id": "r_state",
            "node_id": "r",
            "name": "state",
            "port_type": "state",
            "direction": "out",
            "request_data_schema": sends,
        },
        {
            "id": "m_in",
            "node_id": "m",
            "name": "orchestration",
            "port_type": "in",
            "direction": "in",
        },
        {"id": "e_in", "node_id": "e", "name": "input", "port_type": "in", "direction": "in"},
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "r_in", "kind": "MAINFLOW"},
        {"source_port_id": "r_out", "target_port_id": "e_in", "kind": "MAINFLOW"},
        {"source_port_id": "r_state", "target_port_id": "m_in", "kind": "INVOKE"},
    ]
    return _write(root, {"nodes": nodes, "ports": ports, "edges": edges})


def _contract_errors(report) -> list[str]:
    return [c.message for c in report.errors if c.category == "contract"]


# -- the two helpers, as the platform defines them -----------------------------


def test_contract_becomes_flat_fields_with_required_flags() -> None:
    assert contract_to_fields(_STORY_CONTRACT) == {
        "routed_to": {**_STORY_FIELDS["routed_to"], "required": True},
        "match_reason": {**_STORY_FIELDS["match_reason"], "required": True},
    }
    assert contract_to_fields({"type": "object", "properties": {"x": {"type": "string"}}}) == {
        "x": {"type": "string", "required": False}
    }


@pytest.mark.parametrize(
    "open_contract",
    [
        {"type": "object", "additionalProperties": True},
        {"type": "object", "properties": {}},
        {},
        None,
    ],
)
def test_an_open_object_declares_no_fields(open_contract: Any) -> None:
    assert contract_to_fields(open_contract) is None


def test_a_flat_map_is_returned_unchanged() -> None:
    flat = {"user_prompt": {"type": "string"}, "type": {"type": "string"}}
    assert contract_to_fields(flat) is flat


def test_editor_widget_types_map_to_string() -> None:
    contract = declarations_to_contract(
        [
            {"name": n, "type": t}
            for n, t in zip("abcd", ("text_long", "select", "multi_select", "secret"), strict=True)
        ]
    )
    assert contract is not None
    assert {p["type"] for p in contract["properties"].values()} == {"string"}
    assert declarations_to_contract([{"type": "text"}]) is None
    assert declarations_to_contract([]) is None


# -- precedence: first match wins ----------------------------------------------


def _loaded(start_config=None, start_out=None, end_config=None, end_in=None) -> dict:
    return {
        "nodes": [
            {"id": "s", "component_type": "START", "config": start_config or {}},
            {"id": "e", "component_type": "END", "config": end_config or {}},
        ],
        "ports": [
            {"id": "s_out", "node_id": "s", "direction": "out", **(start_out or {})},
            {"id": "e_in", "node_id": "e", "direction": "in", **(end_in or {})},
        ],
        "component_types": {},
    }


def test_declarations_beat_the_port_on_both_halves() -> None:
    req, resp = child_port_fields(
        _loaded(
            start_config={
                "input_parameters": {
                    "declarations": [{"name": "q", "type": "text_long", "required": True}]
                }
            },
            start_out={"response_data_schema": {"other": {"type": "string"}}},
            end_config={"output_parameters": {"declarations": [{"name": "r", "type": "text"}]}},
            end_in={"request_data_schema": dict(_STORY_FIELDS)},
        )
    )
    assert req == {"q": {"type": "string", "required": True}}
    assert resp == {"r": {"type": "string", "required": False}}


def test_port_schema_beats_its_contract_and_the_contract_is_the_last_resort() -> None:
    req, resp = child_port_fields(
        _loaded(
            start_out={
                "response_data_schema": {"q": {"type": "string"}},
                "contract": {"type": "object", "properties": {"x": {}}},
            },
            end_in={"contract": _STORY_CONTRACT},
        )
    )
    assert req == {"q": {"type": "string"}}
    assert set(resp) == {"routed_to", "match_reason"}
    assert all(f["required"] for f in resp.values())


def test_a_child_without_start_or_end_declares_nothing() -> None:
    loaded = _loaded()
    loaded["nodes"] = loaded["nodes"][:1]
    assert child_port_fields(loaded) == (None, None)


# -- end to end through validate_orchestration --------------------------------


def test_story_teller_router_child_provides_its_end_fields(tmp_path: Path) -> None:
    root = _story_parent(tmp_path, {"request_data_schema": dict(_STORY_FIELDS)})
    report = validate_orchestration(str(root), validate_scripts=False)
    assert report.valid, [(c.id, c.message) for c in report.errors]


def test_story_teller_contract_only_end_is_read_as_fields(tmp_path: Path) -> None:
    root = _story_parent(tmp_path, {"contract": _STORY_CONTRACT})
    report = validate_orchestration(str(root), validate_scripts=False)
    assert report.valid, [(c.id, c.message) for c in report.errors]


def test_a_child_that_returns_less_than_the_parent_needs_is_rejected(tmp_path: Path) -> None:
    root = _story_parent(
        tmp_path, {"request_data_schema": {"routed_to": _STORY_FIELDS["routed_to"]}}
    )
    report = validate_orchestration(str(root), validate_scripts=False)
    assert _contract_errors(report) == [
        "Required field 'match_reason' in target request_data_schema is not "
        "provided by source response_data_schema."
    ]


def test_classifier_member_with_optional_prompt_is_invokable(tmp_path: Path) -> None:
    decls = [{"label": "User Prompt", "name": "user_prompt", "type": "text_long"}]
    root = _classifier_parent(tmp_path, decls, {"user_prompt": {"type": "string"}})
    report = validate_orchestration(str(root), validate_scripts=False)
    assert _contract_errors(report) == []


def test_classifier_member_requiring_a_field_the_router_omits_is_rejected(tmp_path: Path) -> None:
    decls = [{"name": "user_prompt", "type": "text_long", "required": True}]
    root = _classifier_parent(tmp_path, decls, {"other": {"type": "string"}})
    errors = _contract_errors(validate_orchestration(str(root), validate_scripts=False))
    assert len(errors) == 1, errors
    assert "'user_prompt'" in errors[0], errors


def test_classifier_member_type_mismatch_is_rejected(tmp_path: Path) -> None:
    decls = [{"name": "user_prompt", "type": "text_long", "required": True}]
    root = _classifier_parent(tmp_path, decls, {"user_prompt": {"type": "integer"}})
    errors = _contract_errors(validate_orchestration(str(root), validate_scripts=False))
    assert len(errors) == 1, errors
    assert "type mismatch" in errors[0], errors
