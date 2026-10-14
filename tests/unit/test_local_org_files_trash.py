"""A local org-files delete without ``permanent`` moves the item to the trash, not away."""

from __future__ import annotations

from pathlib import Path

from perceivo.config import Config, FilesConfig
from perceivo.local.adapters.org_files import LocalOrgFilesService
from perceivo.local.context import LocalExtensionManager


def _trashed(trash: Path, relative: str) -> list[Path]:
    return [p for p in trash.glob(f"*/{relative}") if p.exists()]


async def test_a_file_moves_to_the_trash_keeping_its_path(tmp_path):
    org = LocalOrgFilesService(str(tmp_path / "org"), trash_dir=str(tmp_path / "trash"))
    (tmp_path / "org" / "a").mkdir()
    (tmp_path / "org" / "a" / "f.txt").write_text("kept")
    await org.delete("a/f.txt")
    assert not (tmp_path / "org" / "a" / "f.txt").exists()
    [moved] = _trashed(tmp_path / "trash", "a/f.txt")
    assert moved.read_text() == "kept"


async def test_a_folder_moves_to_the_trash(tmp_path):
    org = LocalOrgFilesService(str(tmp_path / "org"), trash_dir=str(tmp_path / "trash"))
    (tmp_path / "org" / "d").mkdir()
    (tmp_path / "org" / "d" / "f.txt").write_text("kept")
    await org.delete("d")
    assert not (tmp_path / "org" / "d").exists()
    [moved] = _trashed(tmp_path / "trash", "d/f.txt")
    assert moved.read_text() == "kept"


async def test_two_deletes_of_one_name_both_survive(tmp_path):
    org = LocalOrgFilesService(str(tmp_path / "org"), trash_dir=str(tmp_path / "trash"))
    for text in ("one", "two"):
        (tmp_path / "org" / "f.txt").write_text(text)
        await org.delete("f.txt")
    assert sorted(p.read_text() for p in _trashed(tmp_path / "trash", "f.txt")) == ["one", "two"]


async def test_a_permanent_delete_leaves_nothing(tmp_path):
    org = LocalOrgFilesService(str(tmp_path / "org"), trash_dir=str(tmp_path / "trash"))
    (tmp_path / "org" / "f.txt").write_text("gone")
    await org.delete("f.txt", permanent=True)
    assert not (tmp_path / "org" / "f.txt").exists()
    assert _trashed(tmp_path / "trash", "f.txt") == []


async def test_the_trash_sits_under_the_local_runner_root(tmp_path):
    ext = LocalExtensionManager(Config(root=str(tmp_path), files=FilesConfig()))
    org_dir = tmp_path / "files" / "org"
    (org_dir / "f.txt").write_text("kept")
    await ext.org_files.delete("f.txt")
    [moved] = _trashed(tmp_path / "files" / "trash", "f.txt")
    assert moved.read_text() == "kept"
    assert "f.txt" not in [e["name"] for e in await ext.org_files.list("")]
