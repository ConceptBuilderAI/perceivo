"""After a full scaffold, run and registry install, nothing is written outside the home.

User-named outputs (the files ``init`` and ``new`` scaffold) are taken before the
snapshot; everything the runner, the file planes and the registry write after it
must land under ``<project>/.perceivo``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from perceivo._cli import main as cli
from perceivo.component import registry
from perceivo.runner import run_orchestration

ORCHESTRATION = "orchestration"
HELLO = "hello"
HELPER = "helper"
CONFIG_FILE = "perceivo.yaml"
UTF8 = "utf-8"
DOT_PERCEIVO = ".perceivo"
QUERY_INPUT = '{"query": "hi"}'


def _invoke(*args: str) -> None:
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output


def _tree(root: Path) -> set[Path]:
    """Every path under ``root``, except the interpreter's own bytecode cache.

    Importing a component script lets Python write ``__pycache__`` beside it
    unless ``PYTHONDONTWRITEBYTECODE`` is set; that is the interpreter, not the
    SDK, so it is not a runner write.
    """
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if "__pycache__" not in path.relative_to(root).parts
    }


@pytest.fixture
def scaffolded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("PERCEIVO_HOME", raising=False)
    monkeypatch.delenv("PERCEIVO_CONFIG", raising=False)
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.chdir(root)
    _invoke("init")
    _invoke("new", ORCHESTRATION, HELLO)
    (root / HELPER).mkdir()
    monkeypatch.chdir(root / HELPER)
    _invoke("new", "tool", HELPER)
    monkeypatch.chdir(root)
    return root


def test_nothing_outside_home_is_written(scaffolded, monkeypatch):
    before = _tree(scaffolded)
    _invoke("run", ORCHESTRATION, HELLO, "--input", QUERY_INPUT)
    monkeypatch.chdir(scaffolded / HELLO)
    _invoke("run", ORCHESTRATION, ".", "--input", QUERY_INPUT)
    monkeypatch.chdir(scaffolded)
    registry.install(str(scaffolded / HELPER))

    written = _tree(scaffolded) - before
    outside = sorted(str(p) for p in written if p.parts[0] != DOT_PERCEIVO)
    assert outside == []
    assert len(list((scaffolded / DOT_PERCEIVO / "runs").glob("*.json"))) == 2
    assert (scaffolded / DOT_PERCEIVO / "registry" / HELPER).is_dir()


def test_one_yaml_and_one_env_serve_runs_from_the_root_and_the_project(scaffolded, monkeypatch):
    found = sorted(p.name for p in scaffolded.rglob("*") if p.name in (CONFIG_FILE, ".env"))
    assert found == [".env", CONFIG_FILE]
    # The root perceivo.yaml turns real secrets on, and the root .env holds one.
    with (scaffolded / CONFIG_FILE).open("a", encoding=UTF8) as handle:
        handle.write("\nmocks:\n  modes:\n    secrets: local\n")
    with (scaffolded / ".env").open("a", encoding=UTF8) as handle:
        handle.write("GREETING=hola\n")
    agent = scaffolded / HELLO / "agent.py"
    agent.write_text(
        agent.read_text(encoding=UTF8).replace(
            'query = input.request_data.get("query", "")',
            'query = await perceivo.secrets.get("GREETING")',
        ),
        encoding=UTF8,
    )
    answers = []
    for cwd in (scaffolded, scaffolded / HELLO):
        monkeypatch.chdir(cwd)
        result = asyncio.run(run_orchestration(str(scaffolded / HELLO), input_data={"query": "x"}))
        answers.append(result.response_data.get("result"))
    assert answers == ["hola", "hola"]
