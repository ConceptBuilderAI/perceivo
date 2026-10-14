"""The local-runner root (``PERCEIVO_HOME``), its scratch space and file paths."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from perceivo._internal import home as home_mod
from perceivo._internal.config_paths import file_roots as _file_roots
from perceivo._internal.home import home, session_tmp, subdir
from perceivo.config import load
from perceivo.orchestration._builder import Orchestration
from perceivo.types.errors import ConfigurationError

UTF8 = "utf-8"
HOME_ENV = "PERCEIVO_HOME"
DOT_PERCEIVO = ".perceivo"
YAML = "perceivo.yaml"


@pytest.fixture
def no_home_env(monkeypatch):
    monkeypatch.delenv(HOME_ENV, raising=False)
    monkeypatch.delenv("PERCEIVO_CONFIG", raising=False)


def _yaml(directory: Path, text: str = "version: 1\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / YAML
    path.write_text(text, encoding=UTF8)
    return path


def test_env_wins_over_everything(tmp_path, monkeypatch, no_home_env):
    _yaml(tmp_path / "a", "root: elsewhere\n")
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "h"))
    assert home(tmp_path / "a") == (tmp_path / "h").resolve()


def test_yaml_root_is_relative_to_the_yaml(tmp_path, monkeypatch, no_home_env):
    _yaml(tmp_path / "a", "root: ../state\n")
    monkeypatch.chdir(tmp_path)
    assert home(tmp_path / "a" / "deep") == (tmp_path / "state").resolve()


def test_dot_perceivo_beside_the_discovered_yaml(tmp_path, monkeypatch, no_home_env):
    _yaml(tmp_path / "a")
    (tmp_path / "a" / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    assert home(tmp_path / "a" / "sub") == (tmp_path / "a").resolve() / DOT_PERCEIVO


def test_cwd_when_no_yaml_applies(tmp_path, monkeypatch, no_home_env):
    monkeypatch.chdir(tmp_path)
    assert home() == (tmp_path / DOT_PERCEIVO).resolve()


def test_explicit_config_path_wins_over_discovery(tmp_path, monkeypatch, no_home_env):
    named = _yaml(tmp_path / "named")
    _yaml(tmp_path / "project")
    assert home(tmp_path / "project", config_path=named) == named.parent / DOT_PERCEIVO


def test_resolving_creates_nothing(tmp_path, monkeypatch, no_home_env):
    monkeypatch.chdir(tmp_path)
    subdir("runs")
    assert list(tmp_path.iterdir()) == []


def test_a_non_string_root_is_refused(tmp_path, no_home_env):
    _yaml(tmp_path / "a", "root: 3\n")
    target = tmp_path / "a"
    with pytest.raises(ConfigurationError, match="root must be a non-empty string"):
        home(target)


def test_subdir_rejects_a_name_outside_the_layout():
    with pytest.raises(ConfigurationError, match="home layout"):
        subdir("elsewhere")


def test_yaml_relative_file_paths(tmp_path, monkeypatch, no_home_env):
    """``files.working_dir: ../x`` in a/perceivo.yaml, run from b/, is a/../x."""
    config_file = _yaml(tmp_path / "a", "files:\n  working_dir: ../x\n")
    # b/ sits one level deeper than a/, so b/../x and a/../x are different places.
    (tmp_path / "deep" / "b").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "deep" / "b")
    working, org = _file_roots(load(str(config_file)))
    assert Path(working) == (tmp_path / "x").resolve()
    assert Path(org) == (tmp_path / "a").resolve() / DOT_PERCEIVO / "files" / "org"


def test_default_file_roots_are_under_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "h"))
    config = load(str(tmp_path / "absent.yaml"))
    assert config.root == str((tmp_path / "h").resolve())
    working, _ = _file_roots(config)
    assert Path(working) == (tmp_path / "h").resolve() / "files" / "working"


def test_dotenv_defaults_to_the_one_beside_the_yaml(tmp_path, monkeypatch):
    config_file = _yaml(tmp_path / "a")
    monkeypatch.chdir(tmp_path)
    expected = str((tmp_path / "a" / ".env").resolve())
    assert load(str(config_file)).secrets.dotenv_path == expected


# ---------------------------------------------------------------------------
# Scratch: <home>/tmp/<session>/
# ---------------------------------------------------------------------------


def test_session_tmp_is_under_home_and_removed(isolated_perceivo_home):
    with session_tmp("t_") as scratch:
        assert scratch.parent == isolated_perceivo_home / "tmp"
        assert scratch.is_dir()
    assert not scratch.exists()


def test_session_tmp_is_removed_when_the_body_raises(isolated_perceivo_home):
    holder: list[Path] = []
    with pytest.raises(RuntimeError, match="body failed"):
        _raise_inside_session(holder)
    assert not holder[0].exists()


def _raise_inside_session(holder: list[Path]) -> None:
    with session_tmp("t_") as scratch:
        holder.append(scratch)
        raise RuntimeError("body failed")


def test_stale_sessions_are_reaped_and_young_ones_kept(isolated_perceivo_home):
    tmp_root = isolated_perceivo_home / "tmp"
    stale, young = tmp_root / "crashed-old", tmp_root / "crashed-young"
    stale.mkdir(parents=True)
    young.mkdir()
    old = time.time() - home_mod.STALE_AFTER_SECONDS - 60
    os.utime(stale, (old, old))
    with session_tmp("t_"):
        survivors = sorted(p.name for p in tmp_root.iterdir() if p.name.startswith("crashed"))
    assert survivors == ["crashed-young"]


async def test_inmemory_run_scratch_is_removed(isolated_perceivo_home):
    tmp_root = isolated_perceivo_home / "tmp"
    result = await Orchestration("mem").run()
    assert result is not None
    assert list(tmp_root.iterdir()) == []


async def test_inmemory_run_scratch_is_removed_when_the_run_raises(
    isolated_perceivo_home, monkeypatch
):
    import perceivo.runner as runner_pkg

    async def boom(*args, **kwargs):
        raise RuntimeError("run exploded")

    monkeypatch.setattr(runner_pkg, "run_orchestration", boom)
    orch = Orchestration("mem")
    with pytest.raises(RuntimeError, match="run exploded"):
        await orch.run()
    assert list((isolated_perceivo_home / "tmp").iterdir()) == []


def test_validate_scratch_is_removed(isolated_perceivo_home):
    Orchestration("mem").validate()
    assert list((isolated_perceivo_home / "tmp").iterdir()) == []


def test_a_git_install_clones_into_session_scratch_and_removes_it(
    isolated_perceivo_home, monkeypatch
):
    from perceivo.component import registry

    clones: list[Path] = []

    def fake_clone(cmd, **kwargs):
        target = Path(cmd[-1])
        clones.append(target)
        (target / "comp.py").write_text('ENTRY_CLASS = "C"\n', encoding=UTF8)
        (target / "comp.yaml").write_text("name: comp\nextension_type: TOOL\n", encoding=UTF8)

    monkeypatch.setattr(registry.subprocess, "run", fake_clone)
    registry.install("https://example.invalid/comp.git")
    assert clones[0].parent == isolated_perceivo_home / "tmp"
    assert list((isolated_perceivo_home / "tmp").iterdir()) == []
    assert (isolated_perceivo_home / "registry" / "comp").is_dir()
