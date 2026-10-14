"""Coverage tests for the local component registry."""

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
        f"ENTRY_CLASS = '{name.title()}'\nclass {name.title()}: pass\n",
        encoding="utf-8",
    )
    (directory / f"{name}.yaml").write_text(
        f"name: {name}\nextension_type: {ctype}\ndescription: d\ntags: [t]\n",
        encoding="utf-8",
    )
    return directory


@pytest.mark.parametrize("scheme", ["http", "ftp", "telnet"])
def test_install_rejects_insecure_sources(reg_dir, scheme):
    with pytest.raises(ValueError, match="Insecure"):
        registry.install(f"{scheme}://example.com/repo.git")


def test_install_happy_and_overwrite(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    meta = registry.install(str(src))
    assert meta["name"] == "embedder"
    assert meta["component_type"] == "TOOL"
    assert (reg_dir / "embedder" / "registry.json").exists()
    # Second install exercises the dest.exists() -> rmtree overwrite branch.
    meta2 = registry.install(str(src))
    assert meta2["name"] == "embedder"


def test_install_no_valid_component_raises(reg_dir, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    empty_str = str(empty)
    with pytest.raises(ValueError, match="No valid component"):
        registry.install(empty_str)


def test_install_missing_source_raises(reg_dir, tmp_path):
    ghost = str(tmp_path / "ghost")
    with pytest.raises(FileNotFoundError):
        registry.install(ghost)


def test_uninstall_present_and_absent(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    assert registry.uninstall("embedder") is True
    assert registry.uninstall("embedder") is False


def test_get_and_get_content_success(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    got = registry.get("embedder")
    assert (got or {}).get("path")
    assert registry.get("missing") is None
    assert "ENTRY_CLASS" in registry.get_script("embedder")
    assert "extension_type" in registry.get_manifest("embedder")
    assert registry.get_script("missing") is None
    assert registry.get_manifest("missing") is None


def test_copy_to_project_and_missing(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    proj = tmp_path / "project"
    dest = registry.copy_to_project("embedder", str(proj), file_name="my_embed")
    assert dest.name == "my_embed.py"
    assert (proj / "my_embed.yaml").exists()
    ghost_dir = str(tmp_path / "p")
    with pytest.raises(ValueError, match="not found in registry"):
        registry.copy_to_project("ghost", ghost_dir)


def test_install_git_no_subpath_and_clone_failure(reg_dir, tmp_path, monkeypatch):
    clone_root = tmp_path / "clone2"
    _make_source(clone_root, name="gitcomp")
    _clone_lands_in(monkeypatch, clone_root)

    class _R:
        returncode = 0

    monkeypatch.setattr(registry.subprocess, "run", lambda *a, **k: _R())
    meta = registry.install("https://github.com/user/repo.git")
    assert meta["name"] == "gitcomp"

    import subprocess as _sp

    def fail_run(cmd, **kwargs):
        raise _sp.CalledProcessError(1, cmd, stderr=b"fatal: clone failed")

    monkeypatch.setattr(registry.subprocess, "run", fail_run)
    with pytest.raises(RuntimeError, match="Failed to clone"):
        registry.install("https://github.com/user/repo.git")


def test_install_name_override(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    meta = registry.install(str(src), name="Custom Name!")
    assert meta["name"] == "custom_name"


def test_install_local_with_subpath(reg_dir, tmp_path):
    base = tmp_path / "monorepo"
    _make_source(base / "tools" / "embedder")
    meta = registry.install(str(base), path="tools/embedder")
    assert meta["name"] == "embedder"


def test_install_git_with_subpath(reg_dir, tmp_path, monkeypatch):
    clone_root = tmp_path / "clone"
    _make_source(clone_root / "sub" / "comp", name="gitcomp")

    _clone_lands_in(monkeypatch, clone_root)

    class _R:
        returncode = 0

    monkeypatch.setattr(registry.subprocess, "run", lambda *a, **k: _R())

    meta = registry.install("https://github.com/user/repo.git", path="sub/comp")
    assert meta["name"] == "gitcomp"


def test_find_component_files_manifest_by_extension_type(tmp_path):
    d = tmp_path / "byext"
    d.mkdir()
    (d / "script.py").write_text("ENTRY_CLASS = 'S'\n", encoding="utf-8")
    # Manifest name differs from script; matched by having extension_type.
    (d / "meta.yaml").write_text("extension_type: tool\nname: s\n", encoding="utf-8")
    script, manifest = registry._find_component_files(d)
    assert script.name == "script.py"
    assert manifest.name == "meta.yaml"


def test_find_component_files_manifest_fallback_first_yaml(tmp_path):
    d = tmp_path / "fallback"
    d.mkdir()
    (d / "script.py").write_text("ENTRY_CLASS = 'S'\n", encoding="utf-8")
    # Differently-named yaml WITHOUT extension_type -> falls to yaml_files[0].
    (d / "notes.yaml").write_text("foo: bar\n", encoding="utf-8")
    script, manifest = registry._find_component_files(d)
    assert script.name == "script.py"
    assert manifest.name == "notes.yaml"


def test_find_component_files_script_without_any_yaml(tmp_path):
    d = tmp_path / "noyaml"
    d.mkdir()
    (d / "script.py").write_text("ENTRY_CLASS = 'S'\n", encoding="utf-8")
    script, manifest = registry._find_component_files(d)
    assert script.name == "script.py"
    assert manifest is None


def test_find_component_files_no_py_uses_first_yaml(tmp_path):
    d = tmp_path / "noscript"
    d.mkdir()
    (d / "only.yaml").write_text("extension_type: tool\n", encoding="utf-8")
    script, manifest = registry._find_component_files(d)
    assert script is None
    assert manifest.name == "only.yaml"


def test_find_component_files_no_entry_class_uses_first_py(tmp_path):
    d = tmp_path / "noentry"
    d.mkdir()
    (d / "plain.py").write_text("x = 1\n", encoding="utf-8")
    (d / "plain.yaml").write_text("name: p\n", encoding="utf-8")
    script, manifest = registry._find_component_files(d)
    assert script.name == "plain.py"
    assert manifest.name == "plain.yaml"


def test_list_installed_skips_bad_entries(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    # Directory without registry.json -> skipped.
    (reg_dir / "no_meta").mkdir()
    # Directory with corrupt registry.json -> skipped via except.
    corrupt = reg_dir / "corrupt"
    corrupt.mkdir()
    (corrupt / "registry.json").write_text("{not json", encoding="utf-8")

    names = [c["name"] for c in registry.list_installed()]
    assert names == ["embedder"]


def test_get_script_and_manifest_missing_files(reg_dir, tmp_path):
    src = _make_source(tmp_path / "src")
    registry.install(str(src))
    # Delete the underlying files but keep registry.json metadata.
    comp_dir = reg_dir / "embedder"
    (comp_dir / "embedder.py").unlink()
    (comp_dir / "embedder.yaml").unlink()
    assert registry.get_script("embedder") is None
    assert registry.get_manifest("embedder") is None
