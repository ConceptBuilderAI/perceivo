"""``validate component`` and ``run component`` never default the type to AGENT.

With no ``--type`` and no manifest, the type is read from the script itself --
its component decorator or its entry class's base. A script that names no type,
or two, is refused with a message that says how to supply one.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli
from perceivo.validator._component_type import (
    CANNOT_INFER,
    ComponentTypeError,
    infer_from_script,
    resolve_component_type,
)

COMPONENT = "component"
VALIDATE = "validate"
AGENT = "AGENT"

REVIEW_DECORATED = """
import perceivo


@perceivo.review
async def approve(input: perceivo.primitives.ReviewInput) -> perceivo.primitives.ReviewOutput:
    return perceivo.primitives.ReviewOutput(artifact=input.artifact, response_data={"seen": True})
"""

TOOL_DECORATED = """
import perceivo


@perceivo.primitives.tool
async def echo(input: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
    return perceivo.primitives.ToolOutput(response_data={"value": input.request_data.get("value")})
"""

ADAPTER_IMPORTED = """
from perceivo.primitives import AdapterInput, AdapterOutput, adapter


@adapter
async def reshape(input: AdapterInput) -> AdapterOutput:
    return AdapterOutput(artifact=input.artifact, response_data={"value": 1})
"""

POLICY_CLASS = """
import perceivo
from perceivo.primitives import PolicyInput, PolicyOutput


class Route(perceivo.primitives.Policy):
    async def run(self, input: PolicyInput) -> PolicyOutput:
        return PolicyOutput(next_state="state_1", artifact=input.artifact)


ENTRY_CLASS = "Route"
"""

ALIASED_BASE = """
from perceivo.intelligence import FeatureComponent as Base


class Features(Base):
    pass
"""

ENTRY_CLASS_SELECTS = """
from perceivo.primitives import Agent, Tool


class Helper(Tool):
    pass


class Main(Agent):
    pass


ENTRY_CLASS = "Main"
"""

NO_COMPONENT = """
def helper():
    return 1
"""

CONFLICTING = """
import perceivo


@perceivo.tool
async def one(input):
    return None


@perceivo.review
async def two(input):
    return None
"""

NOT_PERCEIVO = """
import other


@other.review
async def one(input):
    return None


class Local(Policy):
    pass
"""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (REVIEW_DECORATED, "REVIEW"),
        (TOOL_DECORATED, "TOOL"),
        (ADAPTER_IMPORTED, "ADAPTER"),
        (POLICY_CLASS, "POLICY"),
        (ALIASED_BASE, "FEATURE"),
        (ENTRY_CLASS_SELECTS, AGENT),
    ],
    ids=[
        "review-decorator",
        "tool-qualified",
        "adapter-imported",
        "policy-class",
        "alias",
        "entry",
    ],
)
def test_the_type_is_read_from_the_script(source, expected):
    assert infer_from_script(source) == expected


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (NO_COMPONENT, "declares no component type"),
        (NOT_PERCEIVO, "declares no component type"),
        (CONFLICTING, "declares REVIEW, TOOL"),
        ("def broken(:\n", "does not parse"),
    ],
    ids=["none", "not-perceivo", "conflicting", "syntax-error"],
)
def test_an_uninferrable_script_is_refused_not_defaulted(source, reason):
    with pytest.raises(ComponentTypeError, match=reason) as caught:
        infer_from_script(source)
    assert str(caught.value).startswith(CANNOT_INFER)


def test_explicit_type_wins_and_the_script_is_not_read(tmp_path):
    missing = str(tmp_path / "absent.py")
    assert resolve_component_type(missing, "tool", None) == "TOOL"


def test_manifest_type_wins_over_the_script(tmp_path):
    script = _write(tmp_path, "r.py", REVIEW_DECORATED)
    assert resolve_component_type(script, None, "extension_type: adapter\n") == "ADAPTER"


def test_manifest_without_a_type_falls_through_to_the_script(tmp_path):
    script = _write(tmp_path, "r.py", REVIEW_DECORATED)
    assert resolve_component_type(script, None, "name: x\n") == "REVIEW"


def test_manifest_category_names_the_type(tmp_path):
    # Bundle-shaped manifests name the type under ``category`` only.
    script = _write(tmp_path, "plain.py", NO_COMPONENT)
    assert resolve_component_type(script, None, "category: evaluator\n") == "EVALUATOR"


def test_extension_type_wins_over_category(tmp_path):
    script = _write(tmp_path, "plain.py", NO_COMPONENT)
    manifest = "extension_type: adapter\ncategory: tool\n"
    assert resolve_component_type(script, None, manifest) == "ADAPTER"


def test_validate_component_reads_the_type_from_a_category_only_manifest(tmp_path):
    from perceivo.validator import validate_component

    # The script declares no type, so only the manifest's category can make it START.
    script = _write(tmp_path, "go.py", NO_COMPONENT)
    (tmp_path / "go.yaml").write_text("name: go\ncategory: START\n", encoding="utf-8")
    report = validate_component(script)
    assert not [c.id for c in report.errors if c.id.startswith("VAL-TYPE")]
    # START's own port rule ran, so the type was read as START.
    assert any(c.message.startswith("START must have") for c in report.errors)


@pytest.mark.parametrize(("given", "mismatch"), [("TRAINING", True), ("INFERENCE", False)])
def test_a_category_contradicting_the_given_type_is_a_mismatch(tmp_path, given, mismatch):
    script = _write(tmp_path, "plain.py", NO_COMPONENT)
    manifest = tmp_path / "plain.yaml"
    manifest.write_text("name: plain\ncategory: INFERENCE\n", encoding="utf-8")
    result = CliRunner().invoke(
        perceivo_cli, [VALIDATE, COMPONENT, script, str(manifest), "--type", given]
    )
    assert ("MAN-TYPE" in result.output) is mismatch, result.output
    if mismatch:
        assert "Manifest category 'INFERENCE'" in result.output, result.output


def test_validate_component_infers_the_type_from_the_script_without_a_manifest(tmp_path):
    from perceivo.validator import validate_component

    script = _write(tmp_path, "go.py", START_CLASS)
    report = validate_component(script)
    assert not [c.id for c in report.errors if c.id.startswith("VAL-TYPE")]
    assert report.valid, [c.message for c in report.errors]


def test_an_unreadable_script_is_reported_as_uninferrable(tmp_path):
    missing = str(tmp_path / "absent.py")
    with pytest.raises(ComponentTypeError, match="cannot read"):
        resolve_component_type(missing, None, None)


def _write(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


# --- through the CLI ----------------------------------------------------------


@pytest.mark.parametrize("command", [VALIDATE, "run"])
def test_cli_refuses_a_script_it_cannot_type(tmp_path, command):
    script = _write(tmp_path, "plain.py", NO_COMPONENT)
    result = CliRunner().invoke(perceivo_cli, [command, COMPONENT, script])
    assert result.exit_code == 1
    assert CANNOT_INFER in result.output
    assert AGENT not in result.output


def test_run_component_runs_an_inferred_review(tmp_path):
    script = _write(tmp_path, "approve.py", REVIEW_DECORATED)
    result = CliRunner().invoke(perceivo_cli, ["run", COMPONENT, script, "--json"])
    assert result.exit_code == 0, result.output
    # The local runner's auto-approval only rides over a REVIEW run.
    assert '"approved": true' in result.output


START_CLASS = """
from perceivo.primitives import Start, StartInput, StartOutput


class Go(Start):
    async def start(self, input: StartInput) -> StartOutput:
        return StartOutput(response_data={})


ENTRY_CLASS = "Go"
"""


def test_validate_component_checks_an_inferred_start_against_start(tmp_path):
    script = _write(tmp_path, "go.py", START_CLASS)
    result = CliRunner().invoke(perceivo_cli, [VALIDATE, COMPONENT, script])
    # Checked as an AGENT, this script fails VAL-METHOD-001 (no run()).
    assert result.exit_code == 0, result.output
    assert "VAL-METHOD-001" not in result.output


def test_validate_component_as_agent_is_the_control(tmp_path):
    script = _write(tmp_path, "go.py", START_CLASS)
    result = CliRunner().invoke(perceivo_cli, [VALIDATE, COMPONENT, script, "--type", AGENT])
    assert result.exit_code == 1
    assert "VAL-METHOD-001" in result.output


def test_run_component_explicit_type_still_wins(tmp_path):
    script = _write(tmp_path, "approve.py", REVIEW_DECORATED)
    result = CliRunner().invoke(
        perceivo_cli, ["run", COMPONENT, script, "--type", "TOOL", "--json"]
    )
    # Run as a TOOL, there is no review decision merged in.
    assert '"approved"' not in result.output


def test_an_untyped_component_names_both_manifest_keys(tmp_path):
    from perceivo.validator import validate_component

    report = validate_component(_write(tmp_path, "plain.py", NO_COMPONENT))
    (check,) = [c for c in report.errors if c.id.startswith("VAL-TYPE")]
    assert "'category'" in check.message and "'extension_type'" in check.message
    assert "category: <TYPE>" in check.suggestion
