"""The port rule the platform applies when it creates each component.

Creating a component runs one rule per type and refuses at its first failure,
counting ``port_type`` exactly as written. ``validate_port_rules`` reports that
refusal alone, and runs the case-folding budgets only when it passes; the bundler
refuses a START or END on the same rule. A project is judged on the ports
``publish`` bundles for each component, which include manifest ports its
``orchestration.yaml`` rows omit.
"""

from __future__ import annotations

from typing import Any

import pytest

from perceivo.validator._create_ports import create_refusal
from perceivo.validator._orchestration._bundle_ports import bundled_port_checks
from perceivo.validator._port_rules import validate_port_rules


def _p(*types: str, direction: str | None = None) -> list[dict[str, Any]]:
    port = {} if direction is None else {"direction": direction}
    return [{"port_type": t, **port} for t in types]


_REVIEW_OUT = (
    "REVIEW must have NO OUT port (found 1). Its IN port is bidirectional -- the survey "
    "response returns through the same port. A declared OUT port cannot be connected to "
    "anything, because REVIEW calls no other component."
)

#: Every refusal of every mirrored type, in the platform's order.
_REFUSALS = [
    ("AGENT", [], "AGENT must have exactly 1 IN port (found 0)"),
    ("AGENT", _p("in", "out", "out"), "AGENT can have at most 1 OUT port (found 2)"),
    ("AGENT", _p("in", *["tool"] * 51), "AGENT can have at most 50 TOOL ports (found 51)"),
    ("AGENT", _p("in", "state"), "AGENT cannot have STATE ports"),
    ("AGENT", _p("in", "policy"), "AGENT cannot have POLICY ports"),
    ("FSM", [], "FSM must have exactly 1 IN port (found 0)"),
    ("FSM", _p("in"), "FSM must have exactly 1 POLICY port (found 0)"),
    ("FSM", _p("in", "policy", *["state"] * 51), "FSM can have at most 50 STATE ports (found 51)"),
    ("FSM", _p("in", "policy", *["out"] * 6), "FSM can have at most 5 OUT ports (found 6)"),
    ("FSM", _p("in", "policy", "tool"), "FSM cannot have TOOL ports"),
    ("TOOL", [], "TOOL must have exactly 1 IN port (found 0)"),
    ("TOOL", _p("in", *["tool"] * 51), "TOOL can have at most 50 TOOL ports (found 51)"),
    ("TOOL", _p("in", "out"), "TOOL cannot have OUT ports (tools don't return artifacts)"),
    ("TOOL", _p("in", "state"), "TOOL cannot have STATE ports"),
    ("TOOL", _p("in", "policy"), "TOOL cannot have POLICY ports"),
    ("POLICY", [], "POLICY must have exactly 1 IN port (found 0)"),
    ("POLICY", _p("in", *["tool"] * 11), "POLICY can have at most 10 TOOL ports (found 11)"),
    ("POLICY", _p("in", "out"), "POLICY cannot have OUT ports"),
    ("POLICY", _p("in", "state"), "POLICY cannot have STATE ports"),
    ("POLICY", _p("in", "policy"), "POLICY cannot have nested POLICY ports"),
    ("START", [], "START must have exactly 1 EXTERNAL_IN port (found 0)"),
    ("START", _p("external_in"), "START must have exactly 1 OUT port (found 0)"),
    (
        "START",
        _p("external_in", "out", "in"),
        "START must have exactly 2 ports (EXTERNAL_IN and OUT), found 3",
    ),
    ("END", [], "END must have exactly 1 IN port (found 0)"),
    ("END", _p("in"), "END must have exactly 1 EXTERNAL_OUT port (found 0)"),
    (
        "END",
        _p("in", "external_out", "out"),
        "END must have exactly 2 ports (IN and EXTERNAL_OUT), found 3",
    ),
    ("REVIEW", _p("in", direction="OUT"), _REVIEW_OUT),
    ("REVIEW", [], "REVIEW must have exactly 1 IN port (found 0)"),
    ("REVIEW", _p("in", "out", direction="IN"), _REVIEW_OUT),
    ("REVIEW", _p("in", "state", direction="IN"), "REVIEW cannot have STATE ports"),
    ("REVIEW", _p("in", "tool", direction="IN"), "REVIEW cannot have TOOL ports"),
    ("REVIEW", _p("in", "policy", direction="IN"), "REVIEW cannot have POLICY ports"),
    ("SCORING", _p("in"), "SCORING components cannot define orchestration ports"),
    ("SCORING", _p("IN", "out"), "SCORING components cannot define orchestration ports"),
    ("EVALUATOR", _p("out"), "EVALUATOR components cannot define orchestration ports"),
    ("EVALUATOR", _p("tool", "state"), "EVALUATOR components cannot define orchestration ports"),
]

#: A port set each type is created with.
_ACCEPTED = [
    ("AGENT", _p("in", "out", *["tool"] * 50)),
    ("FSM", _p("in", "policy", *["state"] * 50)),
    ("TOOL", _p("in", *["tool"] * 50)),
    ("POLICY", _p("in", *["tool"] * 10)),
    ("START", _p("external_in", "out")),
    ("END", _p("in", "external_out")),
    ("REVIEW", _p("in")),
    ("SCORING", []),
    ("EVALUATOR", []),
]


@pytest.mark.parametrize(("ctype", "ports", "message"), _REFUSALS)
def test_each_refusal_is_the_platform_message_alone(ctype, ports, message) -> None:
    refusal = create_refusal(ctype, ports)
    assert refusal is not None and refusal.message == message
    assert [c.message for c in validate_port_rules(ctype, ports)] == [message]


@pytest.mark.parametrize(("ctype", "ports"), _ACCEPTED)
def test_each_type_accepts_its_bounds(ctype, ports) -> None:
    assert create_refusal(ctype, ports) is None
    assert validate_port_rules(ctype, ports) == []


def test_a_type_spelled_in_another_case_is_counted_as_no_type() -> None:
    agent = create_refusal("AGENT", _p("IN"))
    assert agent is not None
    assert agent.message == "AGENT must have exactly 1 IN port (found 0)"
    refusal = create_refusal("START", _p("external_in", "out", "EXTERNAL_IN"))
    assert refusal == ("START must have exactly 2 ports (EXTERNAL_IN and OUT), found 3", None, 3)


def test_an_agent_with_in_and_upper_in_passes_creation_and_fails_the_budgets() -> None:
    ports = _p("IN", "in")
    assert create_refusal("AGENT", ports) is None
    assert [c.message for c in validate_port_rules("AGENT", ports)] == [
        "AGENT must have exactly 1 IN port (found 2)"
    ]


def test_an_fsm_out_port_passes_creation_and_fails_the_budgets() -> None:
    """Creation allows an FSM five OUT ports; the budgets the platform applies next allow none."""
    ports = _p("in", "policy", *["out"] * 5)
    assert create_refusal("FSM", ports) is None
    assert [c.message for c in validate_port_rules("FSM", ports)] == [
        "FSM cannot have OUT ports (an FSM's result leaves through FsmOutput.response_data) "
        "(found 5)"
    ]


_UNRULED = ["ADAPTER", "FEATURE", "TRAINING", "INFERENCE", "EVALUATION", "feature", "Training"]


@pytest.mark.parametrize("ctype", _UNRULED)
def test_a_type_without_a_creation_rule_is_not_refused_there(ctype) -> None:
    assert create_refusal(ctype, _p("in", "in")) is None


def _unknown(ctype: str) -> str:
    return (
        f"Unknown component_type '{ctype}'. Must be one of: ADAPTER, AGENT, END, EVALUATION, "
        "EVALUATOR, FEATURE, FSM, INFERENCE, POLICY, REVIEW, SCORING, START, TOOL, TRAINING."
    )


#: Types the validator refuses before any port is counted, in the same words on every path.
_UNKNOWN = ["ITERATOR", "WIDGET", "agent", "LAYER", "ORCHESTRATION", "MANAGER"]


@pytest.mark.parametrize("ctype", _UNKNOWN)
@pytest.mark.parametrize("ports", [[], _p("in", "out")], ids=["no-ports", "ports"])
def test_a_type_the_platform_does_not_know_is_refused_whatever_its_ports(ctype, ports) -> None:
    refusal = create_refusal(ctype, ports)
    assert refusal is not None
    assert refusal.message == _unknown(ctype)


# validate_port_rules reads the type in any case, so a lower-case rule type is known there.
@pytest.mark.parametrize("ctype", [t for t in _UNKNOWN if t != "agent"])
def test_an_unknown_type_is_one_finding_from_validate(ctype) -> None:
    findings = validate_port_rules(ctype, _p("in", "out"))
    assert [c.message for c in findings] == [_unknown(ctype)]


@pytest.mark.parametrize(
    ("category", "head", "note"),
    [
        *[
            (ctype, f"Component 'w' as publish bundles it: {_unknown(ctype)}", False)
            for ctype in _UNKNOWN
        ],
        (
            "SCORING",
            "Component 'w' (SCORING) as publish bundles it: "
            "SCORING components cannot define orchestration ports.",
            True,
        ),
    ],
)
def test_a_bundled_finding_ends_once_and_explains_the_merge_only_for_a_known_type(
    monkeypatch: pytest.MonkeyPatch, category: str, head: str, note: bool
) -> None:
    bundle = {
        "components": {"c": {"name": "w", "category": category, "ports": _p("in")}},
        "nodes": [{"component_ref": "c", "local_id": "n"}],
    }
    monkeypatch.setattr(
        "perceivo._bundle.orchestration.orchestration_to_bundle", lambda _project: bundle
    )
    checks = bundled_port_checks("project", lambda _node: category, set())
    messages = [c.message for c in checks if c.id.startswith("ORCH-BUNDLE-PORTS-")]
    assert len(messages) == 1
    assert messages[0].startswith(head)
    assert not messages[0].startswith(head + ".")
    assert ("merged into the manifest's" in messages[0]) is note
