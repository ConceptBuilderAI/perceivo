"""The two copies of the manifest port-section table must agree.

``perceivo._bundle.component`` and ``perceivo.validator._manifest`` each carry
their own copy of "which manifest section declares which kind of port". That
duplication is deliberate — ``validator`` does not import ``_bundle``, the same
way ``validator/_orchestration/_edges.py`` mirrors ``_DIR_BY_PORT_TYPE``
rather than importing it — and it is only safe if something fails when the two
drift apart. That is this file.

The second half is the non-vacuity half. Reading the wrong key is not a crash:
it returns ``[]``, the port rules then have nothing to judge, and every
component validates clean. That is exactly how the rules came to be inert for
307/307 components the platform ships. So each reader path here is asserted to
return ports, not merely to return.
"""

from __future__ import annotations

import textwrap

import yaml

from perceivo._bundle.component import _DIR_BY_PORT_TYPE, _FIXED_SECTIONS
from perceivo._bundle.component import _TYPED_SECTIONS as _BUNDLE_TYPED_SECTIONS
from perceivo.validator._component_tables import (
    ALL_COMPONENT_TYPES,
    unknown_component_type_message,
)
from perceivo.validator._orchestration._edges import (
    _DIR_BY_PORT_TYPE as _EDGE_DIR_BY_PORT_TYPE,
)
from perceivo.validator._port_rules import (
    _DIRECTION_BY_PORT_TYPE,
    _MANIFEST_PORT_SECTIONS,
    _PORT_BUDGETS,
    _RULE_GROUPS,
    _manifest_ports,
    validate_port_rules,
)

#: The eight manifest port sections the platform reads, verbatim. Written out
#: rather than imported so that this
#: file pins the SDK to the *platform*, not just to its own other copy — if both
#: SDK tables were edited together this would still fail.
PLATFORM_SECTIONS = {
    "input_ports",
    "output_ports",
    "state_ports",
    "policy_ports",
    "tool_ports",
    "review_ports",
    "external_in_ports",
    "external_out_ports",
}


def _singular_or_plural(section: str) -> set[str]:
    """The two spellings the platform treats as one section."""
    return {section, section[:-1] if section.endswith("s") else section + "s"}


# ---------------------------------------------------------------------------
# Table agreement
# ---------------------------------------------------------------------------


def test_the_validator_and_the_bundler_declare_the_same_sections():
    bundle_sections = set(_FIXED_SECTIONS) | set(_BUNDLE_TYPED_SECTIONS)
    assert set(_MANIFEST_PORT_SECTIONS) == bundle_sections


def test_the_two_tables_assign_the_same_type_and_direction():
    bundle: dict[str, tuple[str, str]] = {
        section: (port_type, direction)
        for section, (port_type, direction, _shape) in _FIXED_SECTIONS.items()
    }
    bundle.update(_BUNDLE_TYPED_SECTIONS)
    assert bundle == _MANIFEST_PORT_SECTIONS


def test_the_validator_direction_map_matches_the_bundler():
    assert _DIRECTION_BY_PORT_TYPE == _DIR_BY_PORT_TYPE


def test_the_orchestration_edge_direction_map_matches_the_bundler():
    """The third copy, which this file's docstring named and did not read.

    ``validator/_orchestration/_edges.py`` carries its own literal for the same
    reason ``_port_rules.py`` does. It is load-bearing: ``port_direction`` derives a missing
    ``direction`` from ``port_type`` so a port is judged the way the bundle
    writer is about to stamp it, and a table that drifted from the writer's
    would judge it some other way while every assertion still passed.
    """
    assert _EDGE_DIR_BY_PORT_TYPE == _DIR_BY_PORT_TYPE


def test_every_section_is_one_the_platform_reads():
    """Spelling included. A section the platform does not read is a section
    whose ports it never sees, however well the SDK validates them."""
    for section in _MANIFEST_PORT_SECTIONS:
        assert _singular_or_plural(section) & PLATFORM_SECTIONS, section


def test_the_section_table_covers_every_port_type():
    """No port type may be unreachable from a manifest: a type with no section
    can only be declared in the flat ``ports`` list."""
    assert {pt for pt, _ in _MANIFEST_PORT_SECTIONS.values()} == set(_DIR_BY_PORT_TYPE)


# ---------------------------------------------------------------------------
# The reader actually reads — both shapes, and the aliases
# ---------------------------------------------------------------------------

SECTIONED = textwrap.dedent(
    """
    extension_type: AGENT
    name: demo
    input_ports:
      - port_name: in
        port_type: in
    output_ports:
      - port_name: out
        port_type: out
    tool_ports:
      - port_name: search
      - port_name: fetch
    """
)

FLAT = textwrap.dedent(
    """
    extension_type: AGENT
    name: demo
    ports:
      - key: in
        port_type: in
        direction: IN
      - key: out
        port_type: out
        direction: OUT
      - key: search
        port_type: tool
        direction: OUT
      - key: fetch
        port_type: tool
        direction: OUT
    """
)


def test_the_sectioned_manifest_reads_its_four_ports():
    ports = _manifest_ports(yaml.safe_load(SECTIONED))
    assert [(p["key"], p["port_type"], p["direction"]) for p in ports] == [
        ("in", "in", "IN"),
        ("out", "out", "OUT"),
        ("search", "tool", "OUT"),
        ("fetch", "tool", "OUT"),
    ]


def test_a_flat_ports_list_is_not_read_and_is_a_manifest_error():
    """The flat ``ports:`` list is not a manifest form; the validator says so."""
    from perceivo.validator._manifest import parse_manifest

    assert _manifest_ports(yaml.safe_load(FLAT)) == []
    _, checks = parse_manifest(FLAT, "AGENT")
    assert [c.id for c in checks] == ["MAN-PORTS-001"]


def test_a_singular_section_spelling_is_read_too():
    """``external_in_port`` is singular in the table and carries a lone dict;
    the plural is what several shipped manifests write."""
    singular = _manifest_ports(yaml.safe_load("external_in_port:\n  port_name: entry\n"))
    plural = _manifest_ports(yaml.safe_load("external_in_ports:\n  - port_name: entry\n"))
    assert singular == plural == [{"key": "entry", "port_type": "external_in", "direction": "IN"}]


def test_an_input_port_may_override_its_own_type():
    """``input_ports`` is a typed section: START declares its entry port there
    as ``external_in``, and 15/15 shipped START manifests do exactly that."""
    ports = _manifest_ports(
        yaml.safe_load("input_ports:\n  - port_name: entry\n    port_type: external_in\n")
    )
    assert ports == [{"key": "entry", "port_type": "external_in", "direction": "IN"}]


# ---------------------------------------------------------------------------
# Non-vacuity: the rules must judge what the reader returns
# ---------------------------------------------------------------------------


def test_control_ports_do_not_count_against_the_out_budget():
    """The finding this pins: grouping on direction made every tool port an OUT
    port, so an agent with sixteen tools reported seventeen outputs.
    The platform budgets an AGENT's ``out`` ports 0-1 and its ``tool`` ports
    0-50 separately."""
    ports = _manifest_ports(yaml.safe_load(SECTIONED))
    assert not validate_port_rules("AGENT", ports)


def test_a_second_output_port_is_still_a_finding():
    """Two-arm control for the test above: if the OUT budget stopped counting
    altogether, that test would pass for the wrong reason."""
    # SECTIONED is dedented, so its section items sit at two spaces. Asserting
    # the substitution landed keeps a stale indent from making this vacuous.
    one = "  - port_name: out\n    port_type: out\n"
    source = SECTIONED.replace(one, one + "  - port_name: out2\n    port_type: out\n")
    assert source != SECTIONED, "the second OUT port was never added"
    findings = validate_port_rules("AGENT", _manifest_ports(yaml.safe_load(source)))
    assert any("at most 1 OUT port (found 2)" in c.message for c in findings), [
        c.message for c in findings
    ]


def test_a_manifest_that_declares_no_ports_is_a_finding_not_a_pass():
    """The inert-gate case. ``_manifest_ports`` returns ``[]`` here, and ``[]``
    is a real answer for a TOOL: it needs exactly one IN port."""
    findings = validate_port_rules("TOOL", _manifest_ports({"extension_type": "TOOL"}))
    assert any("exactly 1 IN port (found 0)" in c.message for c in findings), [
        c.message for c in findings
    ]


# ---------------------------------------------------------------------------
# Budget parity: the SDK's table must be the platform's table
# ---------------------------------------------------------------------------

#: The platform's per-type port budgets, verbatim. Transcribed in the
#: platform's own encoding -- an exact count is an ``int``, a range is a
#: ``{"min", "max"}`` dict -- rather than imported or re-expressed, for the same
#: reason as ``PLATFORM_SECTIONS`` above: this file pins the SDK to the
#: *platform*, and if both SDK tables were edited together it would still fail.
#:
#: This exists because the SDK's hand-written rules had silently drifted from it
#: in three ways at once: FSM allowed 5 OUT ports where the platform budgets 0,
#: START checked only ``out`` so it absorbed any number of IN ports, and ADAPTER
#: and REVIEW had no rule at all. A looser local budget is the one direction
#: that must never drift -- it turns a local pass into a 422 on upload.
PLATFORM_PORT_RULES: dict[str, dict[str, object]] = {
    "AGENT": {
        "in": 1,
        "out": {"min": 0, "max": 1},
        "tool": {"min": 0, "max": 50},
        "review": {"min": 0, "max": 1},
        "state": 0,
        "policy": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "TOOL": {
        "in": 1,
        "out": 0,
        "tool": {"min": 0, "max": 50},
        "state": 0,
        "policy": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "FSM": {
        "in": 1,
        "policy": 1,
        "state": {"min": 0, "max": 50},
        "out": 0,
        "tool": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "LAYER": {
        "in": 1,
        "policy": 1,
        "state": {"min": 0, "max": 50},
        "out": 0,
        "tool": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "POLICY": {
        "in": 1,
        "out": 0,
        "tool": {"min": 0, "max": 50},
        "state": 0,
        "policy": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "START": {
        "external_in": 1,
        "out": 1,
        "in": 0,
        "tool": 0,
        "policy": 0,
        "state": 0,
        "review": 0,
        "external_out": 0,
    },
    "END": {
        "in": 1,
        "external_out": 1,
        "out": 0,
        "external_in": 0,
        "tool": 0,
        "policy": 0,
        "state": 0,
        "review": 0,
    },
    "REVIEW": {
        "in": {"min": 0, "max": 1},
        "review": {"min": 0, "max": 1},
        "out": {"min": 0, "max": 1},
        "tool": {"min": 0, "max": 50},
        "state": 0,
        "policy": 0,
        "external_in": 0,
        "external_out": 0,
    },
    "ADAPTER": {
        "in": 1,
        "state": {"min": 0, "max": 50},
        "out": 0,
        "tool": 0,
        "policy": 0,
        "review": 0,
        "external_in": 0,
        "external_out": 0,
    },
}


def _as_sdk_budget(rule: dict[str, object]) -> dict[str, object]:
    """The platform's encoding in the SDK's: ``{"min","max"}`` becomes a tuple."""
    return {
        group: (value["min"], value["max"]) if isinstance(value, dict) else value
        for group, value in rule.items()
    }


def test_the_sdk_budgets_are_the_platform_budgets():
    """Group by group, count by count. Not a subset check — a looser SDK budget
    passes locally and 422s on upload, which is worse than no check at all."""
    expected = {
        ctype: _as_sdk_budget(rule)
        for ctype, rule in PLATFORM_PORT_RULES.items()
        if ctype != "LAYER"  # not an SDK component type; see below
    }
    assert expected == _PORT_BUDGETS


def test_layer_is_not_a_component_type():
    """The platform still carries LAYER as a duplicate of FSM until its data
    migrates. The SDK has no LAYER: no budget, no folding onto FSM, and the
    type is refused before any port is counted."""
    assert PLATFORM_PORT_RULES["LAYER"] == PLATFORM_PORT_RULES["FSM"]
    assert "LAYER" not in _PORT_BUDGETS
    ports = [{"port_type": "in"}, {"port_type": "policy"}, {"port_type": "out"}]
    assert [c.message for c in validate_port_rules("LAYER", ports)] == [
        unknown_component_type_message("LAYER")
    ]
    assert validate_port_rules("FSM", ports)


def test_every_budget_names_every_group():
    """``_budget_findings`` indexes the budget with every group in
    ``_RULE_GROUPS``. A missing key is then a ``KeyError`` on a visible hole
    rather than a rule that quietly does not run — but only if it is missing
    here too, so hold the invariant on both sides."""
    for ctype, budget in _PORT_BUDGETS.items():
        assert set(budget) == set(_RULE_GROUPS), ctype


def test_types_the_platform_does_not_budget_are_not_budgeted_here():
    """SCORING, FEATURE, TRAINING, INFERENCE and EVALUATION are valid component
    types the platform does not budget. Inventing a budget for them would reject
    components the platform accepts — the same drift as a loose budget, pointing
    the other way.

    SCORING and EVALUATOR are refused any port when they are created, so ten IN
    ports are that refusal alone rather than a budget."""
    refused = {"SCORING", "EVALUATOR"}
    unbudgeted = ALL_COMPONENT_TYPES - set(PLATFORM_PORT_RULES)
    assert unbudgeted, "the platform now budgets every type; this test is stale"
    for ctype in sorted(unbudgeted):
        assert ctype not in _PORT_BUDGETS, ctype
        expected = (
            [f"{ctype} components cannot define orchestration ports"] if ctype in refused else []
        )
        findings = validate_port_rules(ctype, [{"port_type": "in"}] * 10)
        assert [c.message for c in findings] == expected, ctype


def test_every_budgeted_type_reports_ten_inbound_ports():
    """The permanent non-vacuity guard.

    Every budgeted type caps ``in`` at 1 or below, so ten IN ports must be a
    finding for all of them. This is the unit-level form of the corpus mutation
    control that found the inert rules: reading the wrong key returns ``[]``,
    and ``[]`` validates clean, so "no findings" is not evidence a rule ran.
    """
    # The platform checks a START's EXTERNAL_IN port before any other, so ten IN
    # ports are refused there as no EXTERNAL_IN port.
    expected = {"START": "EXTERNAL_IN port (found 0)"}
    for ctype in sorted(_PORT_BUDGETS):
        findings = validate_port_rules(ctype, [{"port_type": "in"}] * 10)
        wanted = expected.get(ctype, "IN port (found 10)")
        assert any(wanted in c.message for c in findings), (
            ctype,
            [c.message for c in findings],
        )
