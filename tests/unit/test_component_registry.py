"""Unit tests for perceivo.component.registry (filesystem registry).

The registry lives at ``<home>/registry`` by default; every test
monkeypatches ``_REGISTRY_DIR`` to a temp directory so the user's real
registry is never touched.  Git clones are mocked.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from perceivo.component import registry


@pytest.fixture
def reg_dir(tmp_path, monkeypatch):
    d = tmp_path / "registry"
    monkeypatch.setattr(registry, "_REGISTRY_DIR", d)
    return d


def _clone_lands_in(monkeypatch, directory):
    """Make the install's session scratch directory ``directory``: the fake clone target."""

    @contextlib.contextmanager
    def fake_session_tmp(prefix):
        yield directory

    monkeypatch.setattr(registry, "_session_tmp", fake_session_tmp)


def _make_source(directory: Path, name="embedder", ctype="tool"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(
        f"ENTRY_CLASS = '{name.title()}'\nclass {name.title()}: pass\n", encoding="utf-8"
    )
    (directory / f"{name}.yaml").write_text(
        f"name: {name}\nextension_type: {ctype}\ndescription: d\ntags: [t]\n", encoding="utf-8"
    )
    return directory


def test_safe_name_sanitises():
    assert registry._safe_name("My Tool!!") == "my_tool"
    assert registry._safe_name("") == "component"


def test_install_from_local_dir(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    meta = registry.install(str(src))
    assert meta["name"] == "embedder"
    assert meta["component_type"] == "TOOL"
    assert (reg_dir / "embedder" / "registry.json").exists()


def test_install_overwrites_existing(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    meta = registry.install(str(src))  # second time -> rmtree + recreate
    assert meta["name"] == "embedder"


def test_install_no_component_raises(reg_dir, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    empty_path = str(empty)
    with pytest.raises(ValueError, match="No valid component"):
        registry.install(empty_path)


def test_install_missing_source_raises(reg_dir, tmp_path):
    missing = str(tmp_path / "ghost")
    with pytest.raises(FileNotFoundError):
        registry.install(missing)


def test_list_get_and_uninstall(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))

    listed = registry.list_installed()
    assert any(c["name"] == "embedder" for c in listed)

    got = registry.get("embedder")
    assert got is not None
    assert got["path"]

    assert registry.get("does-not-exist") is None

    assert registry.uninstall("embedder") is True
    assert registry.uninstall("embedder") is False


def test_get_script_and_manifest(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    script = registry.get_script("embedder")
    manifest = registry.get_manifest("embedder")
    assert "ENTRY_CLASS" in script
    assert "extension_type" in manifest
    assert registry.get_script("missing") is None
    assert registry.get_manifest("missing") is None


def test_copy_to_project(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    proj = tmp_path / "project"
    dest = registry.copy_to_project("embedder", str(proj), file_name="my_embed")
    assert dest.name == "my_embed.py"
    assert (proj / "my_embed.yaml").exists()


def test_copy_to_project_missing_raises(reg_dir, tmp_path):
    dest_path = str(tmp_path / "p")
    with pytest.raises(ValueError, match="not found in registry"):
        registry.copy_to_project("ghost", dest_path)


def test_install_from_git_url_mocked(reg_dir, tmp_path, monkeypatch):
    clone_target = tmp_path / "cloned"
    _make_source(clone_target, name="gitcomp")

    def fake_run(cmd, **kwargs):
        # Simulate a successful git clone (files already present at target).
        class _R:
            returncode = 0

        return _R()

    _clone_lands_in(monkeypatch, clone_target)
    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    meta = registry.install("https://github.com/user/repo.git")
    assert meta["name"] == "gitcomp"


def test_install_from_git_clone_failure(reg_dir, tmp_path, monkeypatch):
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        raise _sp.CalledProcessError(1, cmd, stderr=b"fatal: clone failed")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Failed to clone"):
        registry.install("https://github.com/user/repo.git")


def test_find_component_files_prefers_entry_class(reg_dir, tmp_path):
    d = tmp_path / "multi"
    d.mkdir()
    (d / "plain.py").write_text("x = 1\n", encoding="utf-8")
    (d / "real.py").write_text("ENTRY_CLASS = 'R'\nclass R: pass\n", encoding="utf-8")
    (d / "real.yaml").write_text("extension_type: tool\nname: r\n", encoding="utf-8")
    script, manifest = registry._find_component_files(d)
    assert script.name == "real.py"
    assert manifest.name == "real.yaml"


def test_install_records_the_type_a_category_only_manifest_names(reg_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "scorer.py").write_text("ENTRY_CLASS = 'Scorer'\nclass Scorer: pass\n", encoding="utf-8")
    (src / "scorer.yaml").write_text("name: scorer\ncategory: evaluator\n", encoding="utf-8")
    assert registry.install(str(src))["component_type"] == "EVALUATOR"
