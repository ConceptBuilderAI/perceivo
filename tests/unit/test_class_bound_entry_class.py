"""``ENTRY_CLASS = SomeClass`` is accepted exactly where the platform accepts it.

Intelligence components and evaluators are loaded by resolvers that take
ENTRY_CLASS as the class itself or its name; every other type is resolved by
name only. Every resolver instantiates the class, so an instance is refused
everywhere.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from perceivo.runner import run_component
from perceivo.runner.loader import load_component_class
from perceivo.validator._component import validate_source
from tests.unit.test_component_kinds_run_locally import (
    EVALUATION,
    EVALUATOR,
    FEATURE,
    INFERENCE,
    SCORING,
    TRAINING,
)

CLASS_FORM = {
    "FEATURE": (FEATURE, "Features"),
    "TRAINING": (TRAINING, "Train"),
    "INFERENCE": (INFERENCE, "Infer"),
    "EVALUATION": (EVALUATION, "Judge"),
    "EVALUATOR": (EVALUATOR, "KeepPlan"),
}

TOOL = """
from perceivo.primitives import Tool, ToolInput, ToolOutput

class Echo(Tool):
    async def run(self, input: ToolInput) -> ToolOutput:
        return ToolOutput(response_data={})

ENTRY_CLASS = "Echo"
"""


def _bound(body: str, name: str, form: str) -> str:
    source = textwrap.dedent(body)
    old = f'ENTRY_CLASS = "{name}"'
    assert source.count(old) == 1
    return source.replace(old, f"ENTRY_CLASS = {form}")


def _entry_errors(script: str, ctype: str) -> list[str]:
    report = validate_source(script=script, component_type=ctype)
    return [
        c.title for c in report.checks if c.id.startswith("VAL-ENTRY") and c.severity == "error"
    ]


@pytest.mark.parametrize("ctype", sorted(CLASS_FORM))
def test_a_class_form_component_may_bind_the_class_itself(ctype: str) -> None:
    body, name = CLASS_FORM[ctype]
    assert _entry_errors(_bound(body, name, name), ctype) == []


@pytest.mark.parametrize("ctype", sorted(CLASS_FORM))
def test_a_bound_class_is_still_checked_for_its_methods(ctype: str) -> None:
    body, name = CLASS_FORM[ctype]
    script = _bound(body, name, "Missing") + "\nclass Missing:\n    pass\n"
    report = validate_source(script=script, component_type=ctype)
    assert not report.valid
    assert any(c.id.startswith("VAL-") and c.severity == "error" for c in report.checks)


@pytest.mark.parametrize("ctype", sorted(CLASS_FORM))
def test_a_bound_name_that_is_no_class_in_the_script_is_refused(ctype: str) -> None:
    body, name = CLASS_FORM[ctype]
    assert _entry_errors(_bound(body, name, "Ghost"), ctype) == ["ENTRY_CLASS not found"]


@pytest.mark.parametrize("ctype", sorted(CLASS_FORM))
def test_an_instance_is_refused_where_a_class_is_accepted(ctype: str) -> None:
    body, name = CLASS_FORM[ctype]
    assert _entry_errors(_bound(body, name, f"{name}()"), ctype) == ["ENTRY_CLASS is not a string"]


def test_an_orchestration_component_may_not_bind_the_class_itself() -> None:
    assert _entry_errors(_bound(TOOL, "Echo", "Echo"), "TOOL") == ["ENTRY_CLASS is not a string"]


def test_a_scoring_component_may_not_bind_the_class_itself() -> None:
    script = _bound(SCORING, "LengthScore", "LengthScore")
    assert _entry_errors(script, "SCORING") == ["ENTRY_CLASS is not a string"]


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "component.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_the_loader_accepts_a_bound_class_only_when_asked(tmp_path: Path) -> None:
    script = _script(tmp_path, _bound(INFERENCE, "Infer", "Infer"))
    assert type(load_component_class(script, accept_class=True)).__name__ == "Infer"
    with pytest.raises(RuntimeError, match="must be the class name as a string"):
        load_component_class(script)


def test_the_loader_refuses_an_instance_even_when_a_class_is_accepted(tmp_path: Path) -> None:
    script = _script(tmp_path, _bound(INFERENCE, "Infer", "Infer()"))
    with pytest.raises(RuntimeError, match="must be the class name as a string"):
        load_component_class(script, accept_class=True)


async def test_a_bound_class_inference_component_runs_locally(tmp_path: Path) -> None:
    result = await run_component(
        _script(tmp_path, _bound(INFERENCE, "Infer", "Infer")),
        component_type="INFERENCE",
        input_data={"features": {"x": 21}},
    )
    assert result.success, result.error
    assert result.response_data["result"] == {"doubled": 42}


async def test_a_bound_class_tool_still_fails_to_run_locally(tmp_path: Path) -> None:
    result = await run_component(
        _script(tmp_path, _bound(TOOL, "Echo", "Echo")), component_type="TOOL"
    )
    assert not result.success
    assert "must be the class name as a string" in (result.error or "")


async def test_a_bound_class_evaluator_runs_locally(tmp_path: Path) -> None:
    result = await run_component(
        _script(tmp_path, _bound(EVALUATOR, "KeepPlan", "KeepPlan")),
        component_type="EVALUATOR",
        input_data={"run_snapshot": {"output_data": {"ok": True}}, "capability_id": "cap"},
    )
    assert result.success, result.error
    assert result.response_data["score_run"] == {"score": 1.0}


async def test_a_bound_class_scoring_component_still_fails_to_run_locally(tmp_path: Path) -> None:
    result = await run_component(
        _script(tmp_path, _bound(SCORING, "LengthScore", "LengthScore")),
        component_type="SCORING",
        input_data={"output_payload": {"result": "0123456789"}, "run_id": "r1"},
    )
    assert not result.success
    assert "must be the class name as a string" in (result.error or "")
