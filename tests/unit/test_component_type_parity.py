"""The component types the validator accepts are the types the platform creates.

The platform refuses an unknown type on every create and import path with
``Unknown component_type '<type>'. Must be one of: <sorted types>.`` ITERATOR is
such a type: the platform has no component of that type, only a template of that
name built from POLICY, START, FSM and END components. LAYER is one the platform
still accepts for its legacy data and the SDK does not, and it is refused in the
same words. Each refused case has a control that differs only in the type and
must pass, so a validator that refused everything would not pass here.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli
from perceivo.validator import validate_orchestration
from perceivo.validator._component import validate_source

KNOWN = (
    "ADAPTER, AGENT, END, EVALUATION, EVALUATOR, FEATURE, FSM, INFERENCE, POLICY, "
    "REVIEW, SCORING, START, TOOL, TRAINING"
)

AGENT_SCRIPT = textwrap.dedent(
    """
    import perceivo
    @perceivo.agent
    async def my_agent(input):
        return None
    """
)


def _refusal(ctype: str) -> str:
    return f"Unknown component_type '{ctype}'. Must be one of: {KNOWN}."


def _type_errors(ctype: str, manifest_yaml: str | None = None) -> list[str]:
    report = validate_source(script=AGENT_SCRIPT, component_type=ctype, manifest_yaml=manifest_yaml)
    return [c.message for c in report.errors if c.id.startswith("VAL-TYPE")]


@pytest.mark.parametrize(
    ("ctype", "manifest_yaml"),
    [
        pytest.param("ITERATOR", None, id="iterator-no-manifest"),
        pytest.param("ITERATOR", "extension_type: ITERATOR\n", id="iterator-manifest"),
        pytest.param("iterator", None, id="iterator-lowercase"),
        pytest.param("LAYER", None, id="layer-no-manifest"),
        pytest.param("LAYER", "extension_type: LAYER\n", id="layer-manifest"),
    ],
)
def test_a_type_the_platform_does_not_create_is_refused_in_its_words(
    ctype: str, manifest_yaml: str | None
) -> None:
    assert _type_errors(ctype, manifest_yaml) == [_refusal(ctype)]


def test_the_control_type_is_not_refused() -> None:
    assert _type_errors("AGENT") == []
    assert _type_errors("AGENT", "extension_type: AGENT\n") == []


def _scaffold(tmp_path: Path, extension_type: str) -> Path:
    result = CliRunner().invoke(
        perceivo_cli, ["new", "orchestration", "proj", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    project = tmp_path / "proj"
    path = project / "agent.yaml"
    manifest: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["extension_type"] = extension_type
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return project


@pytest.mark.parametrize("ctype", ["ITERATOR", "LAYER"])
def test_a_manifest_only_unknown_type_is_refused_without_scripts(
    tmp_path: Path, ctype: str
) -> None:
    """The node still says AGENT, so nothing judges the component by its node; the
    bundle carries the manifest's type, which the platform refuses on import."""
    report = validate_orchestration(_scaffold(tmp_path, ctype), validate_scripts=False)
    messages = [c.message for c in report.errors]
    assert not report.valid
    assert any(m.endswith(_refusal(ctype)) for m in messages), messages


def test_the_control_project_passes_without_scripts(tmp_path: Path) -> None:
    report = validate_orchestration(_scaffold(tmp_path, "AGENT"), validate_scripts=False)
    assert report.valid, [c.message for c in report.errors]
