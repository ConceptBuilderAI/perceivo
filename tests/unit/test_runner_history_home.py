"""``history()`` lists a run exactly when the run resolved the home it reads.

Each case writes one record the way a run does (``RunHistory(start,
config_path=...)``) and reads it back through ``history(base_dir=...)``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from perceivo.runner import history
from perceivo.runner.history import RunHistory

UTF8 = "utf-8"
CONFIG = "perceivo.yaml"


def _write_run(start: Path, config_path: Path | None = None) -> None:
    runs = RunHistory(start, config_path=config_path)
    runs.complete_run(runs.start_run("orch"))


async def _listed(base_dir: Path) -> int:
    return (await history(base_dir=str(base_dir))).total


def _config(directory: Path, **values: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG
    path.write_text(yaml.safe_dump(values), encoding=UTF8)
    return path


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("PERCEIVO_HOME", raising=False)
    monkeypatch.delenv("PERCEIVO_CONFIG", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    (tmp_path / "proj" / "sub").mkdir(parents=True)
    return tmp_path


async def test_reader_in_a_subdirectory_sees_the_projects_runs(tree):
    proj = tree / "proj"
    _config(proj)
    _write_run(proj)
    assert await _listed(proj / "sub") == 1
    assert (proj / ".perceivo" / "runs").is_dir()


async def test_perceivo_home_wins_over_an_explicit_file(tree, monkeypatch):
    monkeypatch.setenv("PERCEIVO_HOME", str(tree / "named"))
    _write_run(tree / "proj", config_path=_config(tree / "outside"))
    assert await _listed(tree / "elsewhere") == 1
    assert (tree / "named" / "runs").is_dir()


async def test_perceivo_config_places_the_home_beside_the_named_file(tree, monkeypatch):
    monkeypatch.setenv("PERCEIVO_CONFIG", str(_config(tree / "outside")))
    _write_run(tree / "proj")
    assert await _listed(tree / "elsewhere") == 1
    assert (tree / "outside" / ".perceivo" / "runs").is_dir()


async def test_root_moves_the_home_for_writer_and_reader_alike(tree):
    proj = tree / "proj"
    _config(proj, root="state")
    _write_run(proj / "sub")
    assert await _listed(proj) == 1
    assert (proj / "state" / "runs").is_dir()


async def test_without_a_config_file_the_home_is_the_working_directorys(tree, monkeypatch):
    _write_run(tree / "proj")
    assert (tree / "elsewhere" / ".perceivo" / "runs").is_dir()
    assert await _listed(tree / "proj" / "sub") == 1
    monkeypatch.chdir(tree / "proj")
    assert await _listed(tree / "proj") == 0


async def test_explicit_file_outside_the_project_is_not_listed(tree):
    proj = tree / "proj"
    _config(proj)
    _write_run(proj, config_path=_config(tree / "outside"))
    assert await _listed(proj) == 0
    assert (tree / "outside" / ".perceivo" / "runs").is_dir()


async def test_explicit_file_whose_root_names_the_projects_home_is_listed(tree):
    proj = tree / "proj"
    _config(proj)
    outside = _config(tree / "outside", root=str(proj / ".perceivo"))
    _write_run(tree / "elsewhere", config_path=outside)
    assert await _listed(proj) == 1


async def test_explicit_project_config_is_listed(tree):
    proj = tree / "proj"
    config = _config(proj)
    _write_run(tree / "elsewhere", config_path=config)
    assert await _listed(proj / "sub") == 1


async def test_explicit_sibling_file_shares_the_projects_home_and_is_listed(tree):
    proj = tree / "proj"
    _config(proj)
    sibling = proj / "perceivo.dev.yaml"
    sibling.write_text(yaml.safe_dump({}), encoding=UTF8)
    _write_run(tree / "elsewhere", config_path=sibling)
    assert await _listed(proj) == 1


async def test_explicit_file_in_the_working_directory_matches_the_no_config_home(tree, monkeypatch):
    work = tree / "work"
    custom = work / "custom.yaml"
    work.mkdir()
    custom.write_text(yaml.safe_dump({}), encoding=UTF8)
    monkeypatch.chdir(work)
    _write_run(work, config_path=custom)
    assert await _listed(work) == 1
