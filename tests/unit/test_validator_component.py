"""T5: Component validator (validate_component)."""

from __future__ import annotations

import textwrap

from perceivo.validator._component import validate_source


def _validate(script: str, ctype: str = "AGENT", manifest_yaml: str | None = None):
    return validate_source(
        script=textwrap.dedent(script),
        component_type=ctype,
        manifest_yaml=manifest_yaml,
    )


def test_decorator_async_function_is_valid():
    report = _validate("""
        import perceivo
        @perceivo.agent
        async def my_agent(input):
            return None
    """)
    assert report.valid, [c.message for c in report.errors]


def test_decorator_on_sync_function_errors():
    report = _validate("""
        import perceivo
        @perceivo.agent
        def my_agent(input):
            return None
    """)
    assert not report.valid
    assert any(c.id.startswith("VAL-ASYNC") for c in report.errors)


def test_unknown_component_type_errors():
    report = _validate(
        "ENTRY_CLASS = 'X'\nclass X(perceivo.primitives.Agent):\n    async def run(self, i): ...\n",
        ctype="MYSTERY",
    )
    assert any(c.id.startswith("VAL-TYPE") for c in report.errors)


def test_layer_is_not_an_alias_of_fsm():
    report = _validate(
        """
        ENTRY_CLASS = "MyLayer"
        import perceivo
        class MyLayer(perceivo.primitives.FSM):
            async def run(self, input):
                return None
    """,
        ctype="LAYER",
    )
    # LAYER is not a component type: the type check reports it.
    assert any(c.id.startswith("VAL-TYPE") for c in report.errors)
    assert not report.valid


def test_empty_script_errors_for_non_review():
    report = _validate("", ctype="AGENT")
    assert not report.valid
    assert any(c.id.startswith("VAL-SCRIPT") for c in report.errors)


def test_empty_script_ok_for_review():
    report = _validate("", ctype="REVIEW")
    assert report.valid


def test_syntax_error_blocks_remaining_checks():
    report = _validate("def f(:\n  pass\n", ctype="AGENT")
    assert not report.valid
    assert any(c.id.startswith("VAL-SYNTAX") for c in report.errors)


def test_missing_entry_point_errors():
    report = _validate("x = 1\n", ctype="AGENT")
    assert any(c.id.startswith("VAL-ENTRY") for c in report.errors)


def test_entry_class_not_defined_errors():
    report = _validate("ENTRY_CLASS = 'Ghost'\n", ctype="AGENT")
    assert any(c.id.startswith("VAL-ENTRY") for c in report.errors)


def test_class_missing_required_run_method_errors():
    report = _validate(
        """
        ENTRY_CLASS = "X"
        import perceivo
        class X(perceivo.primitives.Agent):
            pass
    """,
        ctype="AGENT",
    )
    assert any(c.id.startswith("VAL-METHOD") for c in report.errors)


def test_class_with_wrong_base_is_warning_not_error():
    report = _validate(
        """
        ENTRY_CLASS = "X"
        class X:
            async def run(self, input):
                return None
    """,
        ctype="AGENT",
    )
    base_warnings = [c for c in report.warnings if c.id.startswith("VAL-BASE")]
    assert base_warnings


def test_sanitizes_bom_and_crlf():
    script = "﻿ENTRY_CLASS = 'X'\r\nimport perceivo\r\nclass X(perceivo.primitives.Agent):\r\n    async def run(self, i): ...\r\n"
    report = validate_source(script=script, component_type="AGENT")
    assert report.valid, [c.message for c in report.errors]


def test_report_to_dict_has_summary():
    report = _validate("x = 1\n", ctype="AGENT")
    d = report.to_dict()
    assert "summary" in d
    assert d["summary"]["errors"] >= 1
    assert "checks" in d
