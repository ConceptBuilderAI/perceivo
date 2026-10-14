"""An orchestration port's ``effective_contract`` overrides its component port's schemas.

The platform's contract check reads each payload schema from the orchestration
port's ``effective_contract`` first and falls back to the component port only
where that half is unset or null. A field supplied only by the override is
provided, and a field required only by the override is required.
"""

from __future__ import annotations

from typing import Any, Optional

from perceivo.orchestration import load_bundle
from perceivo.validator._orchestration import validate_orchestration
from perceivo.validator._orchestration._main import validate_graph

_START = (
    "import perceivo\n"
    "@perceivo.start\n"
    "async def start(i):\n"
    "    return perceivo.primitives.StartOutput(artifact=i.artifact, response_data=i.request_data)\n"
)
_END = (
    "import perceivo\n"
    "@perceivo.end\n"
    "async def end(i):\n"
    "    return perceivo.primitives.EndOutput(artifact=i.artifact, response_data=i.response_data)\n"
)
_AGENT = "import perceivo\n@perceivo.agent\nasync def agent(i):\n    return i\n"
_ANSWER: dict[str, Any] = {"answer": {"type": "string"}}
_MISSING_ANSWER = (
    "Required field 'answer' in target request_data_schema is not provided by source "
    "response_data_schema."
)


def _component(name: str, script: str, ports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "script": script,
        "manifest_yaml": f"extension_type: {name.lower()}\nname: {name}\n",
        "ports": ports,
    }


def _bundle(
    *,
    start_response: dict[str, Any],
    end_request: dict[str, Any],
    start_effective: Optional[dict[str, Any]] = None,
    end_effective: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """START -> END on one MAINFLOW edge, each orchestration port optionally overridden."""
    return {
        "export_type": "orchestration",
        "orchestration": {"name": "Effective contract"},
        "components": {
            "c_start": _component(
                "Start",
                _START,
                [
                    {"key": "trigger", "direction": "IN", "port_type": "external_in"},
                    {
                        "key": "output",
                        "direction": "OUT",
                        "port_type": "out",
                        "response_data_schema": start_response,
                    },
                ],
            ),
            "c_end": _component(
                "End",
                _END,
                [
                    {
                        "key": "input",
                        "direction": "IN",
                        "port_type": "in",
                        "request_data_schema": end_request,
                    },
                    {"key": "response", "direction": "OUT", "port_type": "external_out"},
                ],
            ),
        },
        "nodes": [
            {"local_id": "start", "name": "Start", "component_ref": "c_start", "config": {}},
            {"local_id": "end", "name": "End", "component_ref": "c_end", "config": {}},
        ],
        "ports": [
            {
                "node_ref": "start",
                "component_port_key": "output",
                "component_port_direction": "OUT",
                "effective_contract": start_effective,
            },
            {
                "node_ref": "end",
                "component_port_key": "input",
                "component_port_direction": "IN",
                "effective_contract": end_effective,
            },
        ],
        "edges": [{"source_port_index": 0, "target_port_index": 1, "edge_kind": "MAINFLOW"}],
    }


def _config_hop_bundle(
    agent_response: dict[str, Any], agent_effective: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """START -> AGENT, whose single in-port continues the mainflow to END through config."""
    bundle = _bundle(start_response=_ANSWER, end_request=_ANSWER)
    bundle["components"]["c_agent"] = _component(
        "Agent",
        _AGENT,
        [
            {
                "key": "input",
                "direction": "IN",
                "port_type": "in",
                "request_data_schema": _ANSWER,
                "response_data_schema": agent_response,
            }
        ],
    )
    bundle["nodes"].insert(
        1,
        {
            "local_id": "agent",
            "name": "Agent",
            "component_ref": "c_agent",
            "config": {"mainflow_target_node_ref": "end"},
        },
    )
    bundle["ports"].insert(
        1,
        {
            "node_ref": "agent",
            "component_port_key": "input",
            "component_port_direction": "IN",
            "effective_contract": agent_effective,
        },
    )
    bundle["edges"] = [{"source_port_index": 0, "target_port_index": 1, "edge_kind": "MAINFLOW"}]
    return bundle


def _contract_errors(tmp_path, bundle: dict[str, Any]) -> list[str]:
    report = validate_orchestration(
        str(load_bundle(bundle, tmp_path / "project")), validate_scripts=False
    )
    return [c.message for c in report.checks if c.id.startswith("ORCH-CONTRACT")]


def _invoke_errors(
    source: dict[str, Any], target: dict[str, Any], source_effective: dict[str, Any]
) -> list[str]:
    """Contract errors on one INVOKE edge from an agent to a tool."""
    ports = [
        {"id": "pa", "node_id": "a", "direction": "out", "port_type": "invoke", **source},
        {"id": "pt", "node_id": "t", "direction": "in", "port_type": "invoke", **target},
    ]
    ports[0]["effective_contract"] = source_effective
    report = validate_graph(
        nodes=[{"id": "a", "component_type": "AGENT"}, {"id": "t", "component_type": "TOOL"}],
        edges=[{"source_port_id": "pa", "target_port_id": "pt", "kind": "INVOKE"}],
        ports=ports,
    )
    return [c.message for c in report.checks if c.id.startswith("ORCH-CONTRACT")]


def test_a_field_the_source_override_provides_is_provided(tmp_path):
    bundle = _bundle(
        start_response={},
        end_request=_ANSWER,
        start_effective={"response_data_schema": _ANSWER},
    )

    assert _contract_errors(tmp_path, bundle) == []


def test_a_field_the_target_override_requires_is_required(tmp_path):
    bundle = _bundle(
        start_response=_ANSWER,
        end_request=_ANSWER,
        end_effective={"request_data_schema": {**_ANSWER, "extra": {"type": "string"}}},
    )

    assert _contract_errors(tmp_path, bundle) == [
        "Required field 'extra' in target request_data_schema is not provided by source "
        "response_data_schema."
    ]


def test_a_null_override_half_falls_back_to_the_component_port(tmp_path):
    bundle = _bundle(
        start_response={},
        end_request=_ANSWER,
        start_effective={"request_data_schema": _ANSWER, "response_data_schema": None},
    )

    assert _contract_errors(tmp_path, bundle) == [
        "Required field 'answer' in target request_data_schema is not provided by source "
        "response_data_schema."
    ]


def test_an_empty_override_half_overrides_the_component_port(tmp_path):
    bundle = _bundle(
        start_response=_ANSWER,
        end_request=_ANSWER,
        start_effective={"response_data_schema": {}},
    )

    assert _contract_errors(tmp_path, bundle) == [_MISSING_ANSWER]


def test_a_config_hop_reads_the_override_of_the_node_s_real_port(tmp_path):
    bundle = _config_hop_bundle({}, {"response_data_schema": _ANSWER})

    assert _contract_errors(tmp_path, bundle) == []


def test_a_config_hop_override_that_provides_less_is_an_error(tmp_path):
    bundle = _config_hop_bundle(_ANSWER, {"response_data_schema": {}})

    assert _contract_errors(tmp_path, bundle) == [_MISSING_ANSWER]


def test_an_invoke_request_reads_the_caller_s_override():
    assert (
        _invoke_errors(
            {"request_data_schema": {}},
            {"request_data_schema": _ANSWER},
            {"request_data_schema": _ANSWER},
        )
        == []
    )
    assert _invoke_errors(
        {"request_data_schema": _ANSWER},
        {"request_data_schema": _ANSWER},
        {"request_data_schema": {}},
    ) == [
        "Required field 'answer' in target request_data_schema is not provided by source "
        "request_data_schema."
    ]


def test_an_invoke_response_reads_the_caller_s_override():
    assert (
        _invoke_errors(
            {"response_data_schema": _ANSWER},
            {"response_data_schema": {}},
            {"response_data_schema": {}},
        )
        == []
    )
    assert _invoke_errors(
        {"response_data_schema": {}},
        {"response_data_schema": {}},
        {"response_data_schema": _ANSWER},
    ) == [
        "Required field 'answer' in source response_data_schema is not provided by target "
        "response_data_schema."
    ]
