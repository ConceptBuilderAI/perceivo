"""``validate_port_rules``: how many ports of which kind each component type may
declare.

Split out of ``test_cov_validators_core.py``
along the banner comment that was already there. The engine itself moved at the
same time, from ``perceivo/validator/manifest.py`` to
``perceivo/validator/_port_rules.py``.

The import stays on ``perceivo.validator._manifest`` deliberately. That module
re-exports ``validate_port_rules`` and the re-exported spelling is what
``SURFACE_LOCK.json`` records as the public one, so reaching the function by the
locked name is what makes this file notice if the re-export is ever dropped.

The budgets these tests assert are transcribed from the platform's per-type port
budget; ``tests/unit/test_manifest_port_sections.py`` checks
the table's shape, and this file checks what the table does.
"""

from __future__ import annotations

from perceivo.validator._component_tables import unknown_component_type_message
from perceivo.validator._port_rules import validate_port_rules

# ===========================================================================
# manifest.validate_port_rules
# ===========================================================================


def _in(**extra):
    d = {"direction": "IN"}
    d.update(extra)
    return d


def _out(**extra):
    d = {"direction": "OUT"}
    d.update(extra)
    return d


def _external_in():
    return {"port_type": "external_in"}


def _external_out():
    return {"port_type": "external_out"}


def test_port_rules_start_ok():
    # A START is entered from outside the orchestration: 1 EXTERNAL_IN, 1 OUT,
    # and no inbound data port (the platform's per-type port budget).
    assert validate_port_rules("START", [_out(), _external_in()]) == []


def test_port_rules_start_wrong_out_count():
    checks = validate_port_rules("START", [_out(), _out(), _external_in()])
    assert checks
    assert "START must have exactly 1 OUT" in checks[0].message


def test_port_rules_start_rejects_an_in_port():
    """The gap that left 46 seeded START components judged by nothing.

    A ``_rule_start`` that checks only ``out`` lets a START carry any number
    of inbound data ports and still validate locally -- then fail on import,
    where the platform budgets ``in`` at 0.
    """
    checks = validate_port_rules("START", [_out(), _external_in(), _in()])
    assert [c.message for c in checks] == [
        "START must have exactly 2 ports (EXTERNAL_IN and OUT), found 3"
    ]


def test_port_rules_start_requires_an_external_in_port():
    checks = validate_port_rules("START", [_out()])
    assert any("START must have exactly 1 EXTERNAL_IN" in c.message for c in checks), [
        c.message for c in checks
    ]


def test_port_rules_end_ok():
    # An END returns through its EXTERNAL_OUT port, not a data OUT port.
    assert validate_port_rules("END", [_in(), _external_out()]) == []


def test_port_rules_end_rejects_an_out_port():
    """One seeded END declared an OUT port that was never created.

    ``_rule_end`` checked only ``in``, so the phantom survived in
    ``company-policy-chatbot.json`` until the rules were rebuilt from the
    platform's table.
    """
    checks = validate_port_rules("END", [_in(), _external_out(), _out()])
    assert [c.message for c in checks] == [
        "END must have exactly 2 ports (IN and EXTERNAL_OUT), found 3"
    ]


def test_port_rules_end_wrong_in_count():
    checks = validate_port_rules("END", [])
    assert checks
    assert "END must have exactly 1 IN" in checks[0].message


def test_port_rules_agent_ok():
    assert validate_port_rules("AGENT", [_in(), _out()]) == []


def test_port_rules_agent_missing_in():
    checks = validate_port_rules("AGENT", [_out()])
    assert any("AGENT must have exactly 1 IN" in c.message for c in checks)


def test_port_rules_agent_too_many_out():
    # AGENT out is capped at one, matching the server; two OUT ports error.
    ports = [_in(), _out(), _out()]
    checks = validate_port_rules("AGENT", ports)
    assert any("at most 1 OUT" in c.message for c in checks)


def test_port_rules_agent_with_policy_port_errors():
    ports = [_in(), {"port_type": "policy"}]
    checks = validate_port_rules("AGENT", ports)
    assert any("cannot have POLICY ports" in c.message for c in checks)


def test_port_rules_fsm_ok():
    # 1 IN + 1 POLICY + up to 50 state ports. No OUT port.
    ports = [_in(), {"port_type": "policy"}, *[{"port_type": "state"} for _ in range(5)]]
    assert validate_port_rules("FSM", ports) == []


def test_port_rules_fsm_rejects_any_out_port():
    """The SDK allowed an FSM five OUT ports; the platform allows none.

    The platform's per-type port budget allows an FSM no OUT port, and 0 of the
    26 FSM components the platform seeds declares one. An ``at most 5`` rule would be
    looser than the server, which is the one direction a local validator must
    never drift: it turns a local pass into a 422 after the author pushes.
    """
    ports = [_in(), {"port_type": "policy"}, _out()]
    checks = validate_port_rules("FSM", ports)
    assert [c.message for c in checks] == [
        "FSM cannot have OUT ports (an FSM's result leaves through "
        "FsmOutput.response_data) (found 1)"
    ]


def test_port_rules_fsm_allows_fifty_state_ports_and_rejects_fifty_one():
    base = [_in(), {"port_type": "policy"}]
    assert validate_port_rules("FSM", base + [{"port_type": "state"}] * 50) == []
    checks = validate_port_rules("FSM", base + [{"port_type": "state"}] * 51)
    assert any("at most 50 STATE ports (found 51)" in c.message for c in checks), [
        c.message for c in checks
    ]


def test_port_rules_adapter_ok_and_rejects_an_out_port():
    """ADAPTER had no handler at all, so 10 seeded manifests went unjudged."""
    assert validate_port_rules("ADAPTER", [_in()]) == []
    assert validate_port_rules("ADAPTER", [_in(), {"port_type": "state"}]) == []
    checks = validate_port_rules("ADAPTER", [_in(), _out()])
    assert any("ADAPTER cannot have OUT ports" in c.message for c in checks), [
        c.message for c in checks
    ]


def test_port_rules_adapter_requires_exactly_one_in_port():
    checks = validate_port_rules("ADAPTER", [])
    assert any("ADAPTER must have exactly 1 IN port (found 0)" in c.message for c in checks)


def test_port_rules_review_takes_one_in_port_and_nothing_outbound():
    """The platform creates a REVIEW only with exactly one IN port and no port
    directed OUT, whatever its type: a ``review`` port is OUT by default."""
    assert validate_port_rules("REVIEW", [_in()]) == []
    assert [c.message for c in validate_port_rules("REVIEW", [])] == [
        "REVIEW must have exactly 1 IN port (found 0)"
    ]
    checks = validate_port_rules("REVIEW", [_in(), _out(), {"port_type": "review"}])
    assert [c.message for c in checks] == [
        "REVIEW must have NO OUT port (found 2). Its IN port is bidirectional -- the "
        "survey response returns through the same port. A declared OUT port cannot be "
        "connected to anything, because REVIEW calls no other component."
    ]
    checks = validate_port_rules("REVIEW", [_in(), _in()])
    assert [c.message for c in checks] == ["REVIEW must have exactly 1 IN port (found 2)"]


def test_port_rules_review_rejects_a_policy_port():
    checks = validate_port_rules("REVIEW", [_in(), {"port_type": "policy", "direction": "IN"}])
    assert any("REVIEW cannot have POLICY ports" in c.message for c in checks), [
        c.message for c in checks
    ]


def test_port_rules_layer_is_not_fsm():
    # LAYER does not normalize to FSM: it is refused as a type the SDK does not create.
    checks = validate_port_rules("LAYER", [_in()])
    assert [c.message for c in checks] == [unknown_component_type_message("LAYER")]


def test_port_rules_fsm_missing_in_and_policy():
    # The platform stops at its first refusal, so each is reported alone.
    checks = validate_port_rules("FSM", [])
    assert [c.message for c in checks] == ["FSM must have exactly 1 IN port (found 0)"]
    checks = validate_port_rules("FSM", [_in()])
    assert [c.message for c in checks] == ["FSM must have exactly 1 POLICY port (found 0)"]


def test_port_rules_fsm_state_count_mismatch():
    ports = [
        _in(),
        {"port_type": "policy"},
        {"port_type": "state"},
    ]
    checks = validate_port_rules("FSM", ports, fsm_states=["s1", "s2"])
    assert any("state ports" in c.message for c in checks)


def test_port_rules_fsm_state_count_match_no_error():
    ports = [
        _in(),
        {"port_type": "policy"},
        {"port_type": "state"},
        {"port_type": "state"},
    ]
    checks = validate_port_rules("FSM", ports, fsm_states=["s1", "s2"])
    assert checks == []


def test_port_rules_tool_ok():
    # TOOL cannot have OUT ports (tools don't return artifacts), so a lone IN is valid.
    assert validate_port_rules("TOOL", [_in()]) == []


def test_port_rules_tool_missing_in():
    checks = validate_port_rules("TOOL", [_out()])
    assert any("TOOL must have exactly 1 IN" in c.message for c in checks)


def test_port_rules_tool_any_out_errors():
    # TOOL out is capped at zero, matching the server; even one OUT errors.
    ports = [_in(), _out()]
    checks = validate_port_rules("TOOL", ports)
    assert any("TOOL cannot have OUT ports" in c.message for c in checks)


def test_port_rules_policy_ok():
    assert validate_port_rules("POLICY", [_in()]) == []


def test_port_rules_policy_rejects_any_out_port():
    checks = validate_port_rules("POLICY", [_in(), _out()])
    assert [c.message for c in checks] == ["POLICY cannot have OUT ports"]


def test_port_rules_policy_missing_in():
    checks = validate_port_rules("POLICY", [])
    assert any("POLICY must have exactly 1 IN" in c.message for c in checks)


def test_port_rules_port_type_lowercase_in_out():
    # Ports can also be identified via port_type "in"/"out".
    ports = [{"port_type": "in"}, {"port_type": "out"}]
    assert validate_port_rules("AGENT", ports) == []


def test_port_rules_unbudgeted_type_no_count_checks():
    """A type the platform creates but declines to budget gets no count rule here
    either; inventing a budget for it would reject components the platform accepts.
    """
    assert validate_port_rules("FEATURE", [_in(), _in(), _out()]) == []
