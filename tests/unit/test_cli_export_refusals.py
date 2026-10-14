"""``perceivo export`` and ``perceivo publish`` report a refused project as one line.

Each refusal a bundle build raises for the user's own files ends the command
with ``Error: <reason>``, exit 1 and no traceback. An exception that is not one
of those refusals is an SDK defect and still propagates.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from perceivo._bundle import orchestration as orchestration_bundle
from perceivo._cli import main
from tests.unit.test_validator_orchestration_parity import _project
from tests.unit.test_validator_recorded_ports import MANIFEST_IN_ONLY, _imported


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    return src


def _graph(project: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    path = project / "orchestration.yaml"
    graph = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(graph)
    path.write_text(yaml.safe_dump(graph), encoding="utf-8")
    return project


def _agent(**fields: Any) -> Callable[[dict[str, Any]], None]:
    def change(graph: dict[str, Any]) -> None:
        next(n for n in graph["nodes"] if n["id"] == "agent").update(fields)

    return change


def _malformed_record(tmp_path: Path) -> Path:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    script.with_name("shield.component.json").write_text("{not json", encoding="utf-8")
    return script


def _unnested(tmp_path: Path) -> Path:
    return _graph(_project(tmp_path, out_hop=True), _agent(component_type="ORCHESTRATION"))


def _missing_script(tmp_path: Path) -> Path:
    project = _project(tmp_path, out_hop=True)
    (project / "agent.py").unlink()
    return project


def _mistyped_metadata(tmp_path: Path) -> Path:
    return _graph(_project(tmp_path, out_hop=True), _agent(component_metadata={"tags": 5}))


def _unparsable_graph(tmp_path: Path) -> Path:
    project = _project(tmp_path, out_hop=True)
    (project / "orchestration.yaml").write_text("nodes: [\n", encoding="utf-8")
    return project


def _refused(result: Result, reason: str) -> None:
    assert result.exit_code == 1, result.output
    # A traceback reaches the runner as the original exception, not SystemExit.
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.output.splitlines() == [result.output.rstrip("\n")]
    assert result.output.startswith("Error: ")
    assert reason in result.output


REFUSALS = [
    pytest.param(_malformed_record, "is not valid JSON", id="BundleConfigError"),
    pytest.param(_unnested, "declares no 'project' directory", id="NestedOrchestrationError"),
    pytest.param(_missing_script, "Script not found", id="FileNotFoundError"),
    pytest.param(
        _mistyped_metadata,
        "ComponentExportData is invalid: tags: Input should be a valid list",
        id="ValidationError",
    ),
    pytest.param(_unparsable_graph, "while parsing a flow node", id="YAMLError"),
]


@pytest.mark.parametrize(("build", "reason"), REFUSALS)
def test_export_reports_a_refusal_as_one_line(
    tmp_path: Path, build: Callable[[Path], Path], reason: str
) -> None:
    source = build(_src(tmp_path))
    result = CliRunner().invoke(main, ["export", str(source), "-o", str(tmp_path / "out.json")])
    _refused(result, reason)
    assert not (tmp_path / "out.json").exists()


def test_export_reports_an_output_directory_that_does_not_exist(tmp_path: Path) -> None:
    project = _project(_src(tmp_path), out_hop=True)
    out = tmp_path / "absent" / "out.json"
    _refused(CliRunner().invoke(main, ["export", str(project), "-o", str(out)]), str(out))


def test_publish_reports_a_refusal_as_one_line(tmp_path: Path) -> None:
    project = _unparsable_graph(_src(tmp_path))
    result = CliRunner().invoke(main, ["publish", str(project), "--dry-run"])
    _refused(result, "while parsing a flow node")


def test_a_defect_keeps_its_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def defect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("an SDK defect")

    monkeypatch.setattr(orchestration_bundle, "orchestration_to_bundle", defect)
    project = _project(_src(tmp_path), out_hop=True)
    result = CliRunner().invoke(main, ["export", str(project), "-o", str(tmp_path / "out.json")])
    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)


def test_the_cli_process_prints_no_traceback(tmp_path: Path) -> None:
    script = _malformed_record(_src(tmp_path))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    run = subprocess.run(
        [sys.executable, "-c", "from perceivo._cli import main; main()", "export", str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert run.returncode == 1
    assert "Traceback" not in run.stderr
    assert run.stderr.startswith("Error: ") and "is not valid JSON" in run.stderr
