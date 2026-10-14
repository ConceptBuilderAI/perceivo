"""Declaration-truth checks that the server runs on import and the SDK mirrors.

``VAL-CONTRACT-OPTIONAL`` / ``-PASSTHROUGH`` / ``-DYNAMIC`` and
``VAL-UNVERIFIABLE-RESP`` run in ``validate_component`` too, so a component the
platform import refuses does not validate clean locally. Each positive case here is
paired with the negative control that must stay clean, so a check that fires on
everything fails as surely as one that fires on nothing.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from perceivo.validator._component import validate_source


def _ids(script: str, ctype: str, manifest: dict) -> list[tuple[str, str]]:
    manifest = {"extension_type": ctype, **manifest}
    report = validate_source(
        script=textwrap.dedent(script),
        component_type=ctype,
        manifest_yaml=yaml.safe_dump(manifest),
    )
    return [
        (c.id, c.title)
        for c in report.checks
        if c.id.startswith(("VAL-CONTRACT-OPTIONAL", "VAL-CONTRACT-PASSTHROUGH"))
        or c.id.startswith(("VAL-CONTRACT-DYNAMIC", "VAL-UNVERIFIABLE-RESP"))
    ]


def _report(script: str, ctype: str, manifest: dict):
    return validate_source(
        script=textwrap.dedent(script),
        component_type=ctype,
        manifest_yaml=yaml.safe_dump({"extension_type": ctype, **manifest}),
    )


_OPT_IN_FSM = {
    "input_ports": [
        {
            "port_name": "in",
            "artifact_type_input": "optional(json)",
            "artifact_type_output": "none",
            "request_data": {},
            "response_data": {"answer": "string"},
        }
    ]
}

_FSM_UNGUARDED = """
import perceivo
@perceivo.fsm
def f(input):
    d = input.artifact.data
    return perceivo.FSMOutput(response_data={"answer": d})
"""

_FSM_GUARDED = """
import perceivo
@perceivo.fsm
def f(input):
    d = input.artifact.data if input.artifact else None
    return perceivo.FSMOutput(response_data={"answer": d})
"""


def test_fsm_optional_input_read_unguarded_is_refused():
    report = _report(_FSM_UNGUARDED, "FSM", _OPT_IN_FSM)
    check = next(c for c in report.checks if c.id == "VAL-CONTRACT-OPTIONAL-001")
    assert check.title == "optional(...) artifact input declared, but the script requires one"
    assert (check.severity, check.category) == ("error", "contract")
    assert report.valid is False


def test_fsm_optional_input_read_guarded_is_clean():
    assert _ids(_FSM_GUARDED, "FSM", _OPT_IN_FSM) == []


def test_layer_is_not_checked_as_fsm():
    """LAYER is not an orchestration type any more; the FSM rules do not run."""
    assert _ids(_FSM_UNGUARDED, "LAYER", _OPT_IN_FSM) == []


_START_FORWARD = """
import perceivo
@perceivo.start
def f(input):
    return perceivo.primitives.StartOutput(request_data={"q": input.request_data["q"]}, artifact=input.artifact)
"""


def _start_manifest(out_type: str) -> dict:
    return {
        "external_in_port": {
            "port_name": "ext_in",
            "artifact_type_input": "optional(json)",
            "request_data": {"q": "string"},
        },
        "output_ports": [
            {"port_name": "out", "artifact_type_output": out_type, "request_data": {"q": "string"}}
        ],
    }


def test_start_forwarding_artifact_under_bare_output_is_refused():
    assert _ids(_START_FORWARD, "START", _start_manifest("json")) == [
        (
            "VAL-CONTRACT-OPTIONAL-001",
            "Artifact output declared unconditionally but emitted conditionally",
        )
    ]


def test_start_forwarding_artifact_under_optional_output_is_clean():
    assert _ids(_START_FORWARD, "START", _start_manifest("optional( JSON )")) == []


_EMIT_ALWAYS = """
import perceivo
@perceivo.agent
def f(input):
    return perceivo.primitives.AgentOutput(
        response_data={"answer": 1},
        artifact=perceivo.primitives.Artifact(type=perceivo.primitives.ArtifactType.JSON, data={}),
    )
"""

_EMIT_NONE = """
import perceivo
@perceivo.agent
def f(input):
    return perceivo.primitives.AgentOutput(response_data={"answer": 1})
"""


def _agent_out(out_type: str) -> dict:
    return {
        "input_ports": [
            {"port_name": "in", "request_data": {}, "response_data": {"answer": "string"}}
        ],
        "output_ports": [{"port_name": "out", "artifact_type_output": out_type}],
    }


@pytest.mark.parametrize(
    ("script", "out_type", "expected"),
    [
        (
            _EMIT_ALWAYS,
            "optional(json)",
            "optional(...) artifact output declared, but the script always emits",
        ),
        (
            _EMIT_NONE,
            "optional(json)",
            "optional(...) artifact output declared, but the script emits none",
        ),
        (_EMIT_ALWAYS, "json", None),
        (_EMIT_NONE, "none", None),
    ],
)
def test_optional_output_must_match_emission(script, out_type, expected):
    got = _ids(script, "AGENT", _agent_out(out_type))
    assert got == ([("VAL-CONTRACT-OPTIONAL-001", expected)] if expected else [])


_DICT_UPDATE = """
import perceivo
@perceivo.agent
def f(input):
    rd = dict(input.request_data)
    rd.update(x=1)
    return perceivo.primitives.AgentOutput(response_data=rd)
"""

_PARTIAL_SPREAD = """
import perceivo
@perceivo.agent
def f(input):
    other = {"a": 1}
    return perceivo.primitives.AgentOutput(response_data={**other, "answer": 1})
"""

_PLAIN_IN = {
    "input_ports": [{"port_name": "in", "request_data": {}, "response_data": {"answer": "string"}}]
}


def test_unresolvable_response_data_is_unverifiable():
    report = _report(_DICT_UPDATE, "AGENT", _PLAIN_IN)
    check = next(c for c in report.checks if c.id == "VAL-UNVERIFIABLE-RESP-001")
    assert check.title == "response_data cannot be statically resolved"
    assert (check.severity, check.category) == ("error", "unverifiable")
    assert "The manifest declares: ['answer']" in check.message
    assert report.valid is False


def test_partially_visible_response_data_names_visible_keys():
    report = _report(_PARTIAL_SPREAD, "AGENT", _PLAIN_IN)
    check = next(c for c in report.checks if c.id == "VAL-UNVERIFIABLE-RESP-001")
    assert check.title == "response_data only partially resolvable"
    assert "only ['answer'] are visible" in check.message


def test_literal_response_data_is_clean():
    assert _ids(_EMIT_NONE, "AGENT", _PLAIN_IN) == []


_PASSTHROUGH = """
import perceivo
@perceivo.agent
def f(input):
    request_data = input.request_data
    return perceivo.primitives.AgentOutput(response_data={**request_data, "answer": 1})
"""

_PASSTHROUGH_DROP = """
import perceivo
@perceivo.agent
def f(input):
    rd = dict(input.request_data)
    rd.pop("secret", None)
    return perceivo.primitives.AgentOutput(response_data={**rd, "answer": 1})
"""


def _pt(**extra) -> dict:
    port = {
        "port_name": "in",
        "request_data": {"q": "string", "secret": "string"},
        "response_data": {"answer": "string"},
        "response_data_passthrough": "request_data",
        **extra,
    }
    return {"input_ports": [port]}


def test_verified_passthrough_resolves_the_spread():
    assert _ids(_PASSTHROUGH, "AGENT", _pt()) == []
    # Control: without the declaration the same spread is unverifiable.
    assert [i for i, _ in _ids(_PASSTHROUGH, "AGENT", _PLAIN_IN)] == ["VAL-UNVERIFIABLE-RESP-001"]


def test_false_passthrough_is_refused_and_resolves_nothing():
    # The spread is not request_data, so the capability is false: it is reported
    # AND the spread stays unverifiable (a false capability is not honoured).
    assert _ids(_PARTIAL_SPREAD, "AGENT", _pt()) == [
        (
            "VAL-CONTRACT-PASSTHROUGH-001",
            "response_data_passthrough declared, but nothing is passed through",
        ),
        ("VAL-UNVERIFIABLE-RESP-001", "response_data only partially resolvable"),
    ]


def test_passthrough_must_name_stripped_fields():
    assert _ids(_PASSTHROUGH_DROP, "AGENT", _pt()) == [
        (
            "VAL-CONTRACT-PASSTHROUGH-001",
            "Passthrough declaration does not name the fields it strips",
        ),
        ("VAL-UNVERIFIABLE-RESP-001", "response_data only partially resolvable"),
    ]
    assert _ids(_PASSTHROUGH_DROP, "AGENT", _pt(response_data_passthrough_except="secret")) == []
    assert _ids(
        _PASSTHROUGH_DROP, "AGENT", _pt(response_data_passthrough_except=["secret", "q"])
    ) == [
        (
            "VAL-CONTRACT-PASSTHROUGH-001",
            "Passthrough exception list names fields the script keeps",
        ),
        ("VAL-UNVERIFIABLE-RESP-001", "response_data only partially resolvable"),
    ]


_COMPUTED = """
import perceivo
@perceivo.agent
def f(input):
    k = input.parameters.get("k")
    return perceivo.primitives.AgentOutput(response_data={k: 1})
"""


def _dyn(**extra) -> dict:
    port = {"port_name": "in", "request_data": {}, "response_data": {"answer": "string"}}
    return {"input_ports": [{**port, "response_data_contract": "dynamic", **extra}]}


def test_dynamic_contract_needs_reason_and_resolves_computed_keys():
    assert _ids(_COMPUTED, "AGENT", _PLAIN_IN) == [
        ("VAL-UNVERIFIABLE-RESP-001", "response_data cannot be statically resolved")
    ]
    assert _ids(_COMPUTED, "AGENT", _dyn()) == [
        ("VAL-CONTRACT-DYNAMIC-001", "Dynamic response_data contract declared without a reason"),
        ("VAL-UNVERIFIABLE-RESP-001", "response_data cannot be statically resolved"),
    ]
    assert _ids(_COMPUTED, "AGENT", _dyn(response_data_contract_reason="parameter k")) == []


def test_dynamic_contract_on_fixed_literal_is_refused():
    assert _ids(_EMIT_NONE, "AGENT", _dyn(response_data_contract_reason="x")) == [
        (
            "VAL-CONTRACT-DYNAMIC-001",
            "Dynamic response_data contract declared, but the contract is fixed",
        )
    ]


_NO_MANIFEST_REASON = (
    "Could not verify: with no manifest there are no port declarations to check the "
    "script against, so every script<->manifest contract check is skipped. A script "
    "that uses nothing needs no manifest; this one does not qualify"
)


def _verdicts(script: str, ctype: str, manifest) -> list[tuple[str, str, str, str]]:
    report = validate_source(
        script=textwrap.dedent(script), component_type=ctype, manifest_yaml=manifest
    )
    return [
        (c.id, c.severity, c.category, c.message)
        for c in report.checks
        if c.id.startswith(("VAL-UNVERIFIABLE", "MAN-PARSE"))
    ]


def test_checks_need_a_parsed_manifest():
    # The server runs the declaration checks only against a manifest that parsed.
    # With none at all it reports VAL-UNVERIFIABLE-MANIFEST, because the script
    # returns response_data.
    for manifest in (None, ""):
        assert _verdicts(_DICT_UPDATE, "AGENT", manifest) == [
            (
                "VAL-UNVERIFIABLE-MANIFEST-001",
                "error",
                "unverifiable",
                "This AGENT component supplied no YAML manifest, but its script returns "
                f"Output response_data. {_NO_MANIFEST_REASON}",
            )
        ]
    # An unparseable one is reported on the parse path, and nothing else runs.
    (parse_error,) = _verdicts(_DICT_UPDATE, "AGENT", "input_ports: [unclosed")
    assert parse_error[:3] == ("MAN-PARSE-001", "error", "manifest")


_USES_EVERYTHING = """
import perceivo
@perceivo.tool
async def f(input):
    rd = input.request_data or {}
    data = input.artifact
    return perceivo.primitives.ToolOutput(response_data={"q": rd.get("q")}, artifact=data)
"""


def test_every_contract_surface_a_manifestless_script_uses_is_named():
    assert _verdicts(_USES_EVERYTHING, "TOOL", None) == [
        (
            "VAL-UNVERIFIABLE-MANIFEST-001",
            "error",
            "unverifiable",
            "This TOOL component supplied no YAML manifest, but its script reads "
            "input.request_data fields, returns Output response_data, reads "
            f"input.artifact, returns an artifact. {_NO_MANIFEST_REASON}",
        )
    ]


_USES_NOTHING = """
import perceivo
@perceivo.tool
async def f(input):
    return perceivo.primitives.ToolOutput(response_data={}, artifact=None)
"""

# A whole-dict copy is not a keyed read, and an Output that is built but not
# returned is not a returned response_data.
_COPIES_BUT_DOES_NOT_RETURN = """
import perceivo
from perceivo.validator._component import validate_source
@perceivo.tool
async def f(input):
    rd = dict(input.request_data)
    perceivo.primitives.ToolOutput(response_data=rd)
    return None
"""


@pytest.mark.parametrize("script", [_USES_NOTHING, _COPIES_BUT_DOES_NOT_RETURN])
def test_a_manifestless_script_that_uses_nothing_stays_valid(script: str):
    assert _verdicts(script, "TOOL", None) == []


def test_a_component_type_outside_the_orchestration_adapter_is_not_judged():
    assert _verdicts(_DICT_UPDATE, "SCORING", None) == []
