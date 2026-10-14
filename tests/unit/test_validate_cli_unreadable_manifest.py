"""``perceivo validate component`` names a manifest it cannot read, in export's words."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli

# Declares no component type, so only the manifest could have named one.
_SCRIPT = "class C:\n    async def run(self, inp):\n        return None\nENTRY_CLASS = 'C'\n"

_REASONS = {"not-utf8": "is not UTF-8 text (byte 9)", "directory": "is a directory, not a file"}


def _component(tmp_path: Path, manifest: str) -> Path:
    script = tmp_path / "agent.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    if manifest == "not-utf8":
        (tmp_path / "agent.yaml").write_bytes(b"name: caf\xe9\nextension_type: AGENT\n")
    else:
        (tmp_path / "agent.yaml").mkdir()
    exported = CliRunner().invoke(perceivo_cli, ["export", str(script), "-o", str(tmp_path / "b")])
    assert exported.stderr == f"Error: {tmp_path / 'agent.yaml'} {_REASONS[manifest]}\n"
    return script


@pytest.mark.parametrize("manifest", ["not-utf8", "directory"])
def test_text_report_names_the_manifest(tmp_path: Path, manifest: str) -> None:
    script = _component(tmp_path, manifest)
    result = CliRunner().invoke(perceivo_cli, ["validate", "component", str(script)])
    assert result.exit_code == 1, result.output
    assert result.stderr == ""
    assert f"The manifest cannot be read: agent.yaml {_REASONS[manifest]}" in result.stdout
    assert "MAN-FORMAT-001" in result.stdout
    assert "cannot infer" not in result.stdout
    assert "VAL-TYPE" not in result.stdout


@pytest.mark.parametrize("manifest", ["not-utf8", "directory"])
def test_json_report_names_the_manifest(tmp_path: Path, manifest: str) -> None:
    script = _component(tmp_path, manifest)
    result = CliRunner().invoke(perceivo_cli, ["validate", "component", str(script), "--json"])
    assert result.exit_code == 1, result.output
    assert result.stderr == ""
    checks = json.loads(result.stdout)["checks"]
    assert [(c["id"], c["message"]) for c in checks] == [
        ("MAN-FORMAT-001", f"The manifest cannot be read: agent.yaml {_REASONS[manifest]}")
    ]


def test_no_manifest_and_no_type_is_still_refused(tmp_path: Path) -> None:
    script = tmp_path / "agent.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    result = CliRunner().invoke(perceivo_cli, ["validate", "component", str(script)])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: cannot infer component type")
