"""``VAL-CONTRACT-RESP``: returned ``response_data`` against the output ports.

The platform refuses at import a component whose script returns a field no
output-role port declares, or whose output-role port declares a field the script
never returns. ``validate_component`` checked neither direction, so both
validated clean locally. Each refusal here is paired with a control that must
stay clean.
"""

from __future__ import annotations

import textwrap

import yaml

from perceivo.validator._component import validate_source

_NEVER_RETURNS = "Output port declares response_data the script never returns"
_UNDECLARED = "Undeclared response_data fields in script"

_AGENT_SCRIPT = '''
import perceivo


@perceivo.agent
async def agent(input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    """Answer the incoming request."""
    query = input.request_data.get("query", "")

    return perceivo.primitives.AgentOutput(
        artifact=perceivo.primitives.Artifact(
            type=perceivo.primitives.ArtifactType.JSON,
            data={"query": query},
        ),
        response_data={"answer": query, "result": query},
    )
'''

_FSM_SCRIPT = '''
import perceivo


@perceivo.fsm
async def router(input: perceivo.primitives.FsmInput) -> perceivo.primitives.FsmOutput:
    """State machine - invoke a state and republish its result."""
    result = await perceivo.flow.invoke(
        "state_1",
        request_data=input.request_data,
        artifact=input.artifact,
    )

    return perceivo.primitives.FsmOutput(
        artifact=input.artifact,
        response_data={
            "visited_state": "state_1",
            "state_response": result,
        },
    )
'''


def _agent_manifest(response_data: dict) -> dict:
    return {
        "extension_type": "AGENT",
        "name": "agent",
        "input_ports": [
            {
                "port_name": "input",
                "port_type": "in",
                "artifact_type_input": "json",
                "artifact_type_output": "json",
                "request_data": {"query": {"type": "string"}},
            }
        ],
        "output_ports": [
            {
                "port_name": "output",
                "port_type": "out",
                "artifact_type_input": "json",
                "artifact_type_output": "json",
                "response_data": response_data,
            }
        ],
    }


def _fsm_manifest(response_data: dict) -> dict:
    return {
        "extension_type": "FSM",
        "name": "router",
        "input_ports": [
            {
                "port_name": "input",
                "port_type": "in",
                "artifact_type_input": "json",
                "artifact_type_output": "optional(json)",
                "response_data": response_data,
            }
        ],
        "policy_ports": [
            {"port_name": "policy", "artifact_type_input": "none", "artifact_type_output": "none"}
        ],
        "state_ports": [
            {"port_name": "state_1", "artifact_type_input": "json", "artifact_type_output": "json"},
            {"port_name": "state_2", "artifact_type_input": "json", "artifact_type_output": "json"},
        ],
    }


def _resp(script: str, ctype: str, manifest: dict) -> list[tuple[str, str, str, str]]:
    report = validate_source(
        script=textwrap.dedent(script),
        component_type=ctype,
        manifest_yaml=yaml.safe_dump(manifest),
    )
    return [
        (c.id, c.severity, c.title, c.message)
        for c in report.checks
        if c.id.startswith(("VAL-CONTRACT-RESP", "VAL-UNVERIFIABLE-RESP"))
    ]


_AGENT_DECLARED = {"answer": "string", "result": "string"}
_FSM_DECLARED = {"visited_state": "string", "state_response": "object"}


def test_agent_declared_field_never_returned_is_refused():
    manifest = _agent_manifest({**_AGENT_DECLARED, "confidence": "float"})
    assert _resp(_AGENT_SCRIPT, "AGENT", manifest) == [
        (
            "VAL-CONTRACT-RESP-001",
            "error",
            _NEVER_RETURNS,
            "An output port declares response_data the script's Output never returns: "
            "['confidence']",
        )
    ]


def test_fsm_declared_field_never_returned_is_refused():
    manifest = _fsm_manifest({**_FSM_DECLARED, "route_reason": "string"})
    assert _resp(_FSM_SCRIPT, "FSM", manifest) == [
        (
            "VAL-CONTRACT-RESP-001",
            "error",
            _NEVER_RETURNS,
            "An output port declares response_data the script's Output never returns: "
            "['route_reason']",
        )
    ]


def test_returned_field_not_declared_is_refused():
    script = _AGENT_SCRIPT.replace(
        'response_data={"answer": query, "result": query}',
        'response_data={"answer": query, "result": query, "extra": 1}',
    )
    assert _resp(script, "AGENT", _agent_manifest(_AGENT_DECLARED)) == [
        (
            "VAL-CONTRACT-RESP-001",
            "error",
            _UNDECLARED,
            "Script returns response_data fields not declared on an output port: ['extra']",
        )
    ]


def test_both_directions_number_in_report_order():
    script = _AGENT_SCRIPT.replace('"result": query', '"extra": 1')
    ids = [(i, t) for i, _s, t, _m in _resp(script, "AGENT", _agent_manifest(_AGENT_DECLARED))]
    assert ids == [
        ("VAL-CONTRACT-RESP-001", _UNDECLARED),
        ("VAL-CONTRACT-RESP-002", _NEVER_RETURNS),
    ]


def test_returned_fields_matching_the_declaration_are_clean():
    assert _resp(_AGENT_SCRIPT, "AGENT", _agent_manifest(_AGENT_DECLARED)) == []
    assert _resp(_FSM_SCRIPT, "FSM", _fsm_manifest(_FSM_DECLARED)) == []


def test_keys_returned_through_a_primitives_module_alias_are_read():
    script = """
    import perceivo.primitives as p
    import perceivo
    @perceivo.agent
    def f(input):
        return p.AgentOutput(response_data={"answer": 1, "result": 2, "extra": 3})
    """
    assert [m for *_x, m in _resp(script, "AGENT", _agent_manifest(_AGENT_DECLARED))] == [
        "Script returns response_data fields not declared on an output port: ['extra']"
    ]


def test_unverifiable_response_data_does_not_also_report_unreturned_fields():
    script = """
    import perceivo
    @perceivo.agent
    def f(input):
        other = input.parameters.get("extra_fields")
        return perceivo.primitives.AgentOutput(response_data={**other, "answer": 1})
    """
    manifest = _agent_manifest({**_AGENT_DECLARED, "confidence": "float"})
    assert [i for i, *_x in _resp(script, "AGENT", manifest)] == ["VAL-UNVERIFIABLE-RESP-001"]


def test_verified_dynamic_contract_does_not_report_unreturned_fields():
    script = """
    import perceivo
    @perceivo.agent
    def f(input):
        k = input.parameters.get("k")
        return perceivo.primitives.AgentOutput(response_data={k: 1})
    """
    manifest = _agent_manifest(_AGENT_DECLARED)
    manifest["output_ports"][0].update(
        response_data_contract="dynamic", response_data_contract_reason="parameter k"
    )
    assert _resp(script, "AGENT", manifest) == []


def test_verified_passthrough_satisfies_only_forwarded_fields():
    script = """
    import perceivo
    @perceivo.agent
    def f(input):
        return perceivo.primitives.AgentOutput(
            response_data={**input.request_data, "answer": 1}
        )
    """
    manifest = _agent_manifest({"answer": "string", "query": "string"})
    manifest["output_ports"][0]["response_data_passthrough"] = "request_data"
    assert _resp(script, "AGENT", manifest) == []

    manifest["output_ports"][0]["response_data"]["confidence"] = "float"
    assert [m for *_x, m in _resp(script, "AGENT", manifest)] == [
        "An output port declares response_data the script's Output never returns: ['confidence']"
    ]
