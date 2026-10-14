from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "refresh_example_hashes.py"
PINS = Path("examples/PACKAGE_HASHES.json")
PACKAGES = Path("examples/orchestrations")
SLUGS = ("ask-an-expert", "finish-any-story", "production-line")


def _archive(tmp_path: Path) -> Path:
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "archive", "--format=tar", tree],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    destination = tmp_path / "public-lf"
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            relative = Path(member.name)
            assert not relative.is_absolute()
            assert ".." not in relative.parts
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                target.write_bytes(source.read())
    return destination


def _check(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(HELPER), "--root", str(root), "--source", "worktree", *extra],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _hash_helper():
    spec = importlib.util.spec_from_file_location("refresh_example_hashes", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _zip_package(root: Path, slug: str, destination: Path) -> Path:
    """Re-pack a package folder into the published layout: one top-level <slug>/."""
    archive = destination / f"{slug}.zip"
    package = root / PACKAGES / slug
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(p for p in package.rglob("*") if p.is_file()):
            bundle.writestr(f"{slug}/{path.relative_to(package).as_posix()}", path.read_bytes())
    return archive


def test_lf_git_archive_matches_every_pinned_package(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    completed = _check(root)
    assert completed.returncode == 0, completed.stderr
    pins = json.loads((ROOT / PINS).read_text(encoding="utf-8"))
    assert sorted(pins["packages"]) == list(SLUGS)
    files = sum(len(record["files"]) for record in pins["packages"].values())
    assert f"verified {len(SLUGS)} example packages ({files} files)" in completed.stdout


def test_the_pins_record_the_published_archive_digests() -> None:
    """The pins were taken from these archives; a tree-derived refresh cannot move them."""
    pins = json.loads((ROOT / PINS).read_text(encoding="utf-8"))["packages"]
    assert {slug: record["archive_sha256"] for slug, record in pins.items()} == {
        "ask-an-expert": "858f00615541885ea4ef964c5f9140b3a2dbfaa88ee036448fdbaf1e261c8b04",
        "finish-any-story": "dc31bd55ea57cd2eb5c7948d3bf580fc459b793cee6fde0c469d10181efc13f0",
        "production-line": "6f5c51e2e64577a33bfb2082af095773c0c9d90e72f8a9c024f0bf88a23cfd94",
    }
    assert {slug: len(record["files"]) for slug, record in pins.items()} == {
        "ask-an-expert": 19,
        "finish-any-story": 19,
        "production-line": 35,
    }


@pytest.mark.parametrize("slug", SLUGS)
def test_a_one_byte_change_in_any_package_fails_verification(tmp_path: Path, slug: str) -> None:
    root = _archive(tmp_path)
    for path in sorted(p for p in (root / PACKAGES / slug).rglob("*") if p.is_file()):
        data = path.read_bytes()
        path.write_bytes(data + b" ")
        completed = _check(root)
        path.write_bytes(data)
        assert completed.returncode != 0, path
        assert "package bytes differ from their archive pins" in completed.stderr
        assert path.relative_to(root).as_posix() in completed.stderr
    assert _check(root).returncode == 0


def test_a_file_added_to_or_removed_from_a_package_fails_verification(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    package = root / PACKAGES / SLUGS[0]
    (package / "extra.py").write_text("", newline="\n")
    completed = _check(root)
    assert completed.returncode != 0
    assert f"unpinned file {PACKAGES.as_posix()}/{SLUGS[0]}/extra.py" in completed.stderr

    (package / "extra.py").unlink()
    (package / "input.json").unlink()
    completed = _check(root)
    assert completed.returncode != 0
    assert f"missing file {PACKAGES.as_posix()}/{SLUGS[0]}/input.json" in completed.stderr


def test_a_package_with_no_pins_fails_verification_instead_of_passing_silently(
    tmp_path: Path,
) -> None:
    """Only pinned packages are compared byte for byte, so a folder the pins do
    not name would otherwise ship unchecked; it must fail instead, and so must a
    pinned package whose folder is gone."""
    root = _archive(tmp_path)
    stray = root / PACKAGES / "unpinned-package"
    stray.mkdir()
    (stray / "README.md").write_text("# Unpinned\n", newline="\n")
    completed = _check(root)
    assert completed.returncode != 0
    assert "package folder(s) with no pins: unpinned-package" in completed.stderr

    (stray / "README.md").unlink()
    stray.rmdir()
    for path in sorted((root / PACKAGES / SLUGS[1]).rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (root / PACKAGES / SLUGS[1]).rmdir()
    completed = _check(root)
    assert completed.returncode != 0
    assert f"pinned package(s) absent: {SLUGS[1]}" in completed.stderr


def test_crlf_bytes_in_a_package_file_are_rejected(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    target = root / PACKAGES / SLUGS[2] / "orchestration.yaml"
    lf_bytes = target.read_bytes()
    assert b"\r" not in lf_bytes
    target.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))
    completed = _check(root)
    assert completed.returncode != 0
    assert "package bytes differ from their archive pins" in completed.stderr


def test_pins_rewritten_from_archives_of_the_tree_are_unchanged(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    zips = tmp_path / "zips"
    zips.mkdir()
    archives = [_zip_package(root, slug, zips) for slug in SLUGS]
    rebuilt = _hash_helper().pins_from_archives(archives)["packages"]
    committed = json.loads((ROOT / PINS).read_text(encoding="utf-8"))["packages"]
    assert {slug: record["files"] for slug, record in rebuilt.items()} == {
        slug: record["files"] for slug, record in committed.items()
    }


def test_an_archive_that_differs_from_its_pins_is_rejected(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    zips = tmp_path / "zips"
    zips.mkdir()
    archive = _zip_package(root, SLUGS[0], zips)
    pinned = json.loads((ROOT / PINS).read_text(encoding="utf-8"))["packages"][SLUGS[0]]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() != pinned["archive_sha256"]
    completed = _check(root, "--archive", str(archive))
    assert completed.returncode != 0
    assert f"{SLUGS[0]}.zip does not match its pins" in completed.stderr


def test_public_line_ending_policy_is_exact() -> None:
    assert (ROOT / ".gitattributes").read_bytes() == b"* text=auto eol=lf\n"


def test_write_refuses_to_pin_anything_but_archives() -> None:
    before = (ROOT / PINS).read_bytes()
    completed = subprocess.run(
        [sys.executable, "-B", str(HELPER), "--source", "worktree", "--write"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "--write takes its pins only from --archive files" in completed.stderr
    assert (ROOT / PINS).read_bytes() == before
