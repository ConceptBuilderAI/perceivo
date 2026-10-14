"""``perceivo run orchestration`` refuses a project that nests itself, in validate's words."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli
from tests.unit.test_runner_nested_modes import (
    _END,
    _START,
    _edge,
    _node,
    _parent,
    _ports,
    _write,
)


def _nesting(project: Path, nested: str) -> Path:
    graph = {
        "nodes": [
            _node("s", "START"),
            _node("n", "ORCHESTRATION", project=nested),
            _node("e", "END"),
        ],
        "ports": _ports("s", "n", "e"),
        "edges": [_edge("s", "n"), _edge("n", "e")],
    }
    return _write(project, graph, {"s": _START, "e": _END})


def _refused(project: Path, args: list[str], caplog: pytest.LogCaptureFixture) -> str:
    with caplog.at_level(logging.DEBUG):
        result = CliRunner().invoke(perceivo_cli, ["run", "orchestration", str(project), *args])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.stdout == ""
    assert "Traceback" not in caplog.text
    assert "RecursionError" not in caplog.text
    validated = CliRunner().invoke(perceivo_cli, ["validate", "orchestration", str(project)])
    assert result.stderr == f"Error: {_cycle_message(validated.stdout)}\n"
    return result.stderr


def _cycle_message(report: str) -> str:
    (line,) = [line for line in report.splitlines() if "/ORCH-NESTED-002 " in line]
    return line.split("/ORCH-NESTED-002 ", 1)[1].split(" ", 1)[1]


@pytest.mark.parametrize("args", [[], ["--json"]], ids=["text", "json"])
def test_a_project_that_nests_itself_is_refused(
    tmp_path: Path, args: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    project = _nesting(tmp_path / "self", ".")
    assert _refused(project, args, caplog) == (
        "Error: In nested orchestration 'n': project '.' is already being validated "
        "on this path (self).\n"
    )


@pytest.mark.parametrize("args", [[], ["--json"]], ids=["text", "json"])
def test_two_projects_that_nest_each_other_are_refused(
    tmp_path: Path, args: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    project = _nesting(tmp_path / "a", "../b")
    _nesting(tmp_path / "b", "../a")
    assert _refused(project, args, caplog) == (
        "Error: In nested orchestration 'n': In nested orchestration 'n': project '../a' "
        "is already being validated on this path (a -> b).\n"
    )


def test_a_project_nesting_others_without_a_cycle_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _parent(tmp_path, "subprocess")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(
        perceivo_cli, ["run", "orchestration", ".", "--input", '{"q": "hi"}']
    )
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.stdout
