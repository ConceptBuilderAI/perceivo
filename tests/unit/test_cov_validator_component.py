"""``validate_component``: decorator recognition, entry points, and the manifest
branches reached through them.

Split out of ``test_cov_validators_core.py``
along the banner comment that was already there. Nothing changed in the move.

This is the validator's front door -- it takes component source as a string,
parses it, works out which decorator and entry class it declares, and then runs
the manifest and port checks against what it found. The tests here are about
that first half: what the parser recognises, what it rejects, and which
downstream check each recognition path reaches.
"""

from __future__ import annotations

import textwrap

from perceivo.validator._component import validate_source

# ===========================================================================
# component.validate_component — decorator / manifest / helper branches
# ===========================================================================


def _dedent_validate(script: str, ctype: str = "AGENT", **kwargs):
    return validate_source(script=textwrap.dedent(script), component_type=ctype, **kwargs)


def test_component_direct_decorator_name_valid():
    # @agent (imported directly) recognized as a perceivo decorator.
    report = _dedent_validate(
        """
        from perceivo import agent
        @agent
        async def my_agent(inp):
            return None
        """
    )
    assert report.valid, [c.message for c in report.errors]


def test_component_decorator_call_form_valid():
    # @perceivo.agent() call form recognized.
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent()
        async def my_agent(inp):
            return None
        """
    )
    assert report.valid, [c.message for c in report.errors]


def test_component_entry_class_bare_name_is_rejected():
    # ENTRY_CLASS = SomeName (an ast.Name, not a string constant). The sandbox
    # does getattr(module, module.ENTRY_CLASS), which raises TypeError for a
    # class object, so a component written this way cannot start in the cloud
    # even though it loads under a lenient local loader. The validator must not
    # be the gate that lets it through.
    report = _dedent_validate(
        """
        import perceivo
        class X(perceivo.primitives.Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = X
        """
    )
    assert not report.valid
    messages = " ".join(c.message for c in report.errors)
    assert "ENTRY_CLASS = X" in messages, messages
    assert 'ENTRY_CLASS = "X"' in " ".join(c.suggestion or "" for c in report.errors)


def test_component_entry_class_instance_is_rejected():
    # ENTRY_CLASS = X() -- the form 19 documented examples used. Fails in the
    # sandbox, in the local runner, and here.
    report = _dedent_validate(
        """
        import perceivo
        class X(perceivo.primitives.Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = X()
        """
    )
    assert not report.valid
    assert any("ENTRY_CLASS" in c.message for c in report.errors)


def test_component_entry_class_string_is_accepted():
    # The positive control: without it the two tests above would still pass if
    # the validator started rejecting every ENTRY_CLASS form.
    report = _dedent_validate(
        """
        import perceivo
        class X(perceivo.primitives.Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "X"
        """
    )
    assert report.valid, [c.message for c in report.errors]


def test_component_base_via_attribute_matches():
    # Base referenced as perceivo.primitives.Agent (ast.Attribute) resolves to "Agent".
    report = _dedent_validate(
        """
        import perceivo
        class X(perceivo.primitives.Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "X"
        """
    )
    assert not any(c.id.startswith("VAL-BASE") for c in report.warnings)


def test_component_manifest_extracts_ports_and_applies_rules():
    # manifest_yaml provided (no explicit ports) -> ports extracted -> rules run.
    # START with two OUT ports must produce a PORT-RULE violation.
    manifest = textwrap.dedent(
        """
        extension_type: START
        ports:
          - direction: OUT
          - direction: OUT
        """
    )
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.start
        async def s(inp):
            return None
        """,
        ctype="START",
        manifest_yaml=manifest,
    )
    assert any(c.id.startswith("PORT-RULE") for c in report.checks)


def test_component_explicit_ports_trigger_rules():
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="AGENT",
        ports=[{"direction": "OUT"}],  # missing IN -> rule violation
    )
    assert any(c.id.startswith("PORT-RULE") for c in report.checks)


def test_component_manifest_checks_included():
    # A top-level flat ``ports:`` list is not a manifest form: MAN-PORTS.
    manifest = textwrap.dedent(
        """
        extension_type: AGENT
        ports:
          - base_schema: AgentIn
        """
    )
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="AGENT",
        manifest_yaml=manifest,
    )
    assert any(c.id.startswith("MAN-PORTS") for c in report.checks)


def test_component_non_perceivo_decorators_are_skipped():
    # A function with non-perceivo decorators (attribute + plain name forms)
    # is not treated as an entry point; the class ENTRY_CLASS is used instead.
    report = _dedent_validate(
        """
        import perceivo
        @foo.bar
        @plaindeco
        def helper():
            return 1
        class X(perceivo.primitives.Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "X"
        """,
        ctype="AGENT",
    )
    assert report.valid, [c.message for c in report.errors]


def test_component_decorator_component_after_other_functions():
    # An earlier function with a non-perceivo decorator is walked before the
    # perceivo-decorated one (exercises the decorated-function search loop).
    report = _dedent_validate(
        """
        import perceivo
        @staticmethod
        def other():
            return 1
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="AGENT",
    )
    assert report.valid, [c.message for c in report.errors]


def test_component_entry_class_non_str_non_name_value():
    # ENTRY_CLASS assigned a value that is neither a string constant nor a Name
    # is ignored -> no entry point found.
    report = validate_source(script="ENTRY_CLASS = 123\n", component_type="AGENT")
    assert any(c.id.startswith("VAL-ENTRY") for c in report.errors)


def test_component_base_plain_name_matches():
    # Base referenced as a bare Name (Agent) resolves correctly -> no warning.
    report = _dedent_validate(
        """
        from perceivo.primitives import Agent
        class X(Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "X"
        """,
        ctype="AGENT",
    )
    assert not any(c.id.startswith("VAL-BASE") for c in report.warnings)


def test_component_base_subscript_is_ignored():
    # A subscripted base (Generic-style) is neither Name nor Attribute and is
    # skipped, while perceivo.primitives.Agent still satisfies the base check.
    report = _dedent_validate(
        """
        import perceivo
        class X(mixins[0], perceivo.primitives.Agent):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "X"
        """,
        ctype="AGENT",
    )
    assert not any(c.id.startswith("VAL-BASE") for c in report.warnings)


def test_component_class_based_review_needs_the_review_base():
    # REVIEW has a base class, so a class-based REVIEW that does not
    # inherit ``perceivo.primitives.Review`` draws the base-class warning, and
    # one that does is clean.
    report = _dedent_validate(
        """
        class R:
            async def run(self, inp):
                return None
        ENTRY_CLASS = "R"
        """,
        ctype="REVIEW",
    )
    assert any(c.id.startswith("VAL-BASE") for c in report.warnings)
    assert "perceivo.primitives.Review" in " ".join(c.message for c in report.warnings)
    assert not any(c.id.startswith("VAL-METHOD") for c in report.errors)
    inherited = _dedent_validate(
        """
        import perceivo
        class R(perceivo.primitives.Review):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "R"
        """,
        ctype="REVIEW",
    )
    assert not any(c.id.startswith("VAL-BASE") for c in inherited.warnings)


def test_component_manifest_and_explicit_ports_skips_extraction():
    # When explicit ports are supplied alongside a manifest, the manifest port
    # extraction branch is skipped but manifest checks still run.
    manifest = textwrap.dedent(
        """
        extension_type: AGENT
        input_ports:
          - port_name: input
            port_type: in
        """
    )
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="AGENT",
        manifest_yaml=manifest,
        ports=[{"direction": "IN"}],
    )
    assert report.valid, [c.message for c in report.errors]


def test_component_manifest_ports_not_a_list_is_judged_not_crashed():
    """ports: not_a_list yields no ports, and no ports is a real answer.

    This asserted report.valid while the port rules were skipped whenever
    the port list came back empty — so a manifest whose ports were unreadable
    validated clean, which is the failure mode the rules exist to catch. The
    scalar must still not crash the reader; it must be *judged*, and an AGENT
    with zero IN ports is a finding.
    """
    manifest = textwrap.dedent(
        """
        extension_type: AGENT
        ports: not_a_list
        """
    )
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="AGENT",
        manifest_yaml=manifest,
    )
    assert not report.valid
    messages = [c.message for c in report.errors]
    assert any("AGENT must have exactly 1 IN port (found 0)" in m for m in messages), messages


def test_component_unknown_type_errors():
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="MYSTERY",
    )
    assert any(c.id.startswith("VAL-TYPE") for c in report.errors)


def test_component_unknown_type_with_ports_is_one_finding():
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        async def a(inp):
            return None
        """,
        ctype="MYSTERY",
        ports=[{"direction": "IN"}],
    )
    assert [c.id for c in report.errors if c.id.startswith(("VAL-TYPE", "PORT-RULE"))] == [
        "VAL-TYPE-001"
    ]


def test_component_empty_script_non_review_errors():
    report = validate_source(script="", component_type="AGENT")
    assert not report.valid
    assert any(c.id.startswith("VAL-SCRIPT") for c in report.errors)


def test_component_empty_script_review_ok():
    report = validate_source(script="   \n  ", component_type="REVIEW")
    assert report.valid


def test_component_syntax_error_errors():
    report = validate_source(script="def f(:\n    pass\n", component_type="AGENT")
    assert not report.valid
    assert any(c.id.startswith("VAL-SYNTAX") for c in report.errors)


def test_component_no_entry_point_errors():
    report = validate_source(script="x = 1\n", component_type="AGENT")
    assert any(c.id.startswith("VAL-ENTRY") for c in report.errors)


def test_component_entry_class_not_found_errors():
    report = validate_source(script="ENTRY_CLASS = 'Ghost'\n", component_type="AGENT")
    assert any(c.id.startswith("VAL-ENTRY") for c in report.errors)


def test_component_missing_required_method_errors():
    report = _dedent_validate(
        """
        import perceivo
        class X(perceivo.primitives.Agent):
            pass
        ENTRY_CLASS = "X"
        """,
        ctype="AGENT",
    )
    assert any(c.id.startswith("VAL-METHOD") for c in report.errors)


def test_component_wrong_base_is_warning():
    report = _dedent_validate(
        """
        class X:
            async def run(self, inp):
                return None
        ENTRY_CLASS = "X"
        """,
        ctype="AGENT",
    )
    assert any(c.id.startswith("VAL-BASE") for c in report.warnings)


def test_component_sync_decorator_errors():
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.agent
        def a(inp):
            return None
        """,
        ctype="AGENT",
    )
    assert any(c.id.startswith("VAL-ASYNC") for c in report.errors)


def test_component_layer_is_an_unknown_type():
    report = _dedent_validate(
        """
        import perceivo
        class MyLayer(perceivo.primitives.FSM):
            async def run(self, inp):
                return None
        ENTRY_CLASS = "MyLayer"
        """,
        ctype="LAYER",
    )
    assert any(c.id.startswith("VAL-TYPE") for c in report.errors)


def test_component_sanitizes_bom_and_crlf():
    script = (
        "﻿ENTRY_CLASS = 'X'\r\nimport perceivo\r\n"
        "class X(perceivo.primitives.Agent):\r\n    async def run(self, i): ...\r\n"
    )
    report = validate_source(script=script, component_type="AGENT")
    assert report.valid, [c.message for c in report.errors]


def test_component_decorator_without_function_still_ok():
    # Decorated function is async -> the VAL-ASYNC branch is not triggered,
    # execution flows through to the end (valid report).
    report = _dedent_validate(
        """
        import perceivo
        @perceivo.tool
        async def t(inp):
            return None
        """,
        ctype="TOOL",
    )
    assert report.valid, [c.message for c in report.errors]
