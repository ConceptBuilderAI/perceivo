"""Coverage tests for public CLI commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli

from .fixtures_runner_coverage import (
    _AGENT,
    _AGENT_DICT_ARTIFACT,
    _AGENT_EMPTY,
    _MANIFEST,
    _NO_ENTRY,
    _TOOL_NO_ARTIFACT,
)
from .test_runner_orchestration_coverage import _build_linear

# ---------------------------------------------------------------------------
# CLI: run component / run orchestration
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


def test_run_component_cli_success_scalar_artifact(runner, tmp_path):
    script = tmp_path / "a.py"
    script.write_text(_AGENT, encoding="utf-8")
    (tmp_path / "a.yaml").write_text(_MANIFEST, encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["run", "component", str(script), "--type", "AGENT", "--input", '{"x": 1}'],
    )
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.output
    assert "Artifact type" in result.output
    assert "Response data" in result.output


def test_run_component_cli_dict_artifact(runner, tmp_path):
    script = tmp_path / "a.py"
    script.write_text(_AGENT_DICT_ARTIFACT, encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["run", "component", str(script), "--type", "AGENT"],
    )
    assert result.exit_code == 0, result.output
    assert "Artifact data" in result.output


def test_run_component_cli_explicit_manifest(runner, tmp_path):
    script = tmp_path / "a.py"
    script.write_text(_AGENT, encoding="utf-8")
    manifest = tmp_path / "custom.yaml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["run", "component", str(script), "--type", "AGENT", "--manifest", str(manifest)],
    )
    assert result.exit_code == 0, result.output


def test_run_component_cli_tool_no_artifact(runner, tmp_path):
    script = tmp_path / "t.py"
    script.write_text(_TOOL_NO_ARTIFACT, encoding="utf-8")
    result = runner.invoke(perceivo_cli, ["run", "component", str(script), "--type", "TOOL"])
    assert result.exit_code == 0, result.output
    # No artifact printed, but response data is.
    assert "Artifact type" not in result.output
    assert "Response data" in result.output


def test_run_component_cli_artifact_without_data_or_response(runner, tmp_path):
    script = tmp_path / "a.py"
    script.write_text(_AGENT_EMPTY, encoding="utf-8")
    result = runner.invoke(perceivo_cli, ["run", "component", str(script), "--type", "AGENT"])
    assert result.exit_code == 0, result.output
    assert "Artifact type" in result.output
    # data is None and response_data empty -> neither data nor response printed.
    assert "Artifact data" not in result.output
    assert "Response data" not in result.output


def test_run_component_cli_input_file(runner, tmp_path):
    script = tmp_path / "a.py"
    script.write_text(_AGENT, encoding="utf-8")
    infile = tmp_path / "in.json"
    infile.write_text('{"y": 2}', encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["run", "component", str(script), "--type", "AGENT", "--input-file", str(infile)],
    )
    assert result.exit_code == 0, result.output


def test_run_component_cli_failure_exits_nonzero(runner, tmp_path):
    script = tmp_path / "bad.py"
    script.write_text(_NO_ENTRY, encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["run", "component", str(script), "--type", "AGENT"],
    )
    assert result.exit_code == 1
    assert "RESULT: FAIL" in result.output


def test_run_orchestration_cli_success(runner, tmp_path):
    _build_linear(tmp_path)
    result = runner.invoke(
        perceivo_cli,
        ["run", "orchestration", str(tmp_path), "--input", '{"q": 1}'],
    )
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.output


def test_run_orchestration_cli_input_file(runner, tmp_path):
    _build_linear(tmp_path)
    infile = tmp_path / "in.json"
    infile.write_text('{"q": 9}', encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["run", "orchestration", str(tmp_path), "--input-file", str(infile)],
    )
    assert result.exit_code == 0, result.output


def test_run_orchestration_cli_failure_exits_nonzero(runner, tmp_path):
    result = runner.invoke(
        perceivo_cli,
        ["run", "orchestration", str(tmp_path / "missing")],
    )
    assert result.exit_code == 1
    assert "RESULT: FAIL" in result.output


# ---------------------------------------------------------------------------
# CLI: publish (mocked network)
# ---------------------------------------------------------------------------


def test_publish_cli_dry_run(runner, tmp_path):
    fake = SimpleNamespace(
        dry_run=True,
        bundle={"components": [1, 2, 3]},
        orchestration_id=None,
        response=None,
    )
    with patch(
        "perceivo.orchestration.publish.publish",
        new=AsyncMock(return_value=fake),
    ):
        result = runner.invoke(perceivo_cli, ["publish", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "components: 3" in result.output


def test_publish_cli_uploads_and_prints_response(runner, tmp_path):
    fake = SimpleNamespace(
        dry_run=False,
        bundle={"components": []},
        orchestration_id="orch-123",
        response={"orchestration_id": "orch-123"},
    )
    with patch(
        "perceivo.orchestration.publish.publish",
        new=AsyncMock(return_value=fake),
    ):
        result = runner.invoke(
            perceivo_cli,
            ["publish", str(tmp_path), "--name", "x", "--base-url", "http://127.0.0.1"],
        )
    assert result.exit_code == 0, result.output
    assert "orch-123" in result.output


def test_publish_cli_uploads_without_response(runner, tmp_path):
    fake = SimpleNamespace(
        dry_run=False,
        bundle={"components": []},
        orchestration_id="orch-9",
        response=None,
    )
    with patch(
        "perceivo.orchestration.publish.publish",
        new=AsyncMock(return_value=fake),
    ):
        result = runner.invoke(perceivo_cli, ["publish", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "orch-9" in result.output


# ---------------------------------------------------------------------------
# CLI: validate component / orchestration (uncovered branches)
# ---------------------------------------------------------------------------


def test_validate_component_no_manifest_no_type_defaults_agent(runner, tmp_path):
    runner.invoke(perceivo_cli, ["new", "agent", "a", "--dir", str(tmp_path)])
    with_manifest = runner.invoke(
        perceivo_cli,
        ["validate", "component", str(tmp_path / "a.py"), str(tmp_path / "a.yaml")],
    )
    assert with_manifest.exit_code == 0, with_manifest.output
    assert "PASSED" in with_manifest.output
    # An omitted manifest argument reads a.yaml beside the script, as export does.
    defaulted = runner.invoke(perceivo_cli, ["validate", "component", str(tmp_path / "a.py")])
    assert defaulted.exit_code == 0, defaulted.output
    assert "PASSED" in defaulted.output
    (tmp_path / "a.yaml").unlink()
    result = runner.invoke(perceivo_cli, ["validate", "component", str(tmp_path / "a.py")])
    # The type defaults to AGENT. The scaffolded script reads request_data and
    # returns response_data, so without its manifest the platform's verdict is
    # VAL-UNVERIFIABLE-MANIFEST -- and it names the defaulted type.
    assert result.exit_code == 1, result.output
    assert "VAL-UNVERIFIABLE-MANIFEST-001" in result.output
    assert "This AGENT component supplied no YAML manifest" in result.output


def test_validate_component_autodetects_type_from_valid_manifest(runner, tmp_path):
    runner.invoke(perceivo_cli, ["new", "tool", "t", "--dir", str(tmp_path)])
    # No --type: type is auto-detected from the manifest's extension_type.
    result = runner.invoke(
        perceivo_cli,
        ["validate", "component", str(tmp_path / "t.py"), str(tmp_path / "t.yaml")],
    )
    assert result.exit_code == 0, result.output


def test_validate_component_invalid_manifest_yaml_falls_back_to_agent(runner, tmp_path):
    runner.invoke(perceivo_cli, ["new", "agent", "a", "--dir", str(tmp_path)])
    bad_manifest = tmp_path / "broken.yaml"
    bad_manifest.write_text("bad: [unclosed", encoding="utf-8")
    result = runner.invoke(
        perceivo_cli,
        ["validate", "component", str(tmp_path / "a.py"), str(bad_manifest)],
    )
    # The CLI's type auto-detect hits yaml.YAMLError and falls back to AGENT;
    # the validator then still rejects the unparseable manifest -> exit 1.
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_validate_orchestration_valid_passes(runner, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _build_linear(proj)
    result = runner.invoke(
        perceivo_cli,
        ["validate", "orchestration", str(proj), "--no-scripts"],
    )
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output


def test_validate_orchestration_invalid_exits_nonzero(runner, tmp_path):
    # No orchestration.yaml -> validation fails -> SystemExit(1).
    result = runner.invoke(perceivo_cli, ["validate", "orchestration", str(tmp_path)])
    assert result.exit_code == 1
    assert "FAILED" in result.output
