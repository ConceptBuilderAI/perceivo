"""T7: CLI commands (init, new, validate)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# `perceivo init`
# ---------------------------------------------------------------------------


def test_init_creates_perceivo_yaml_and_env(runner, tmp_workspace):
    result = runner.invoke(perceivo_cli, ["init", "--dir", str(tmp_workspace)])
    assert result.exit_code == 0
    assert (tmp_workspace / "perceivo.yaml").exists()
    assert (tmp_workspace / ".env").exists()


def test_init_is_idempotent_does_not_overwrite(runner, tmp_workspace):
    (tmp_workspace / "perceivo.yaml").write_text("custom", encoding="utf-8")
    runner.invoke(perceivo_cli, ["init", "--dir", str(tmp_workspace)])
    assert (tmp_workspace / "perceivo.yaml").read_text(encoding="utf-8") == "custom"


# ---------------------------------------------------------------------------
# `perceivo new <type> <name>`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ctype", ["agent", "tool", "start", "end", "fsm", "policy"])
def test_new_component_scaffolds_py_and_yaml(runner, tmp_workspace, ctype):
    result = runner.invoke(
        perceivo_cli,
        ["new", ctype, "my_comp", "--dir", str(tmp_workspace)],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_workspace / "my_comp.py").exists()
    assert (tmp_workspace / "my_comp.yaml").exists()


def test_new_agent_script_uses_decorator(runner, tmp_workspace):
    runner.invoke(perceivo_cli, ["new", "agent", "an_a", "--dir", str(tmp_workspace)])
    script = (tmp_workspace / "an_a.py").read_text(encoding="utf-8")
    assert "@perceivo.agent" in script
    assert "async def an_a" in script


def test_new_orchestration_creates_project(runner, tmp_workspace):
    result = runner.invoke(
        perceivo_cli,
        ["new", "orchestration", "my_orch", "--dir", str(tmp_workspace)],
    )
    assert result.exit_code == 0, result.output
    proj = tmp_workspace / "my_orch"
    assert (proj / "orchestration.yaml").exists()


# ---------------------------------------------------------------------------
# `perceivo validate component`
# ---------------------------------------------------------------------------


def test_validate_component_passes_for_valid_decorator_script(runner, tmp_workspace):
    runner.invoke(perceivo_cli, ["new", "agent", "a", "--dir", str(tmp_workspace)])
    result = runner.invoke(
        perceivo_cli,
        ["validate", "component", str(tmp_workspace / "a.py"), str(tmp_workspace / "a.yaml")],
    )
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output


def test_validate_component_exits_nonzero_on_blocked_import(runner, tmp_workspace):
    bad = tmp_workspace / "bad.py"
    bad.write_text(
        "import subprocess\nENTRY_CLASS = 'X'\nclass X:\n    async def run(self, i): ...\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        perceivo_cli,
        ["validate", "component", str(bad), "--type", "AGENT"],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.output


def test_validate_component_autodetects_type_from_manifest(runner, tmp_workspace):
    runner.invoke(perceivo_cli, ["new", "tool", "t", "--dir", str(tmp_workspace)])
    result = runner.invoke(
        perceivo_cli,
        ["validate", "component", str(tmp_workspace / "t.py"), str(tmp_workspace / "t.yaml")],
    )
    # Type should be auto-detected as TOOL from manifest's extension_type field.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# `perceivo validate orchestration`
# ---------------------------------------------------------------------------


def test_validate_orchestration_missing_yaml_errors(runner, tmp_workspace):
    result = runner.invoke(
        perceivo_cli,
        ["validate", "orchestration", str(tmp_workspace)],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.output


def test_main_help_lists_known_commands(runner):
    result = runner.invoke(perceivo_cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "new", "validate", "run", "publish"):
        assert cmd in result.output
