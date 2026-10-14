"""Security tests: local file adapters confine every access to their root.

These guard the path-traversal hardening (Sonar S2083): orchestration- and
seed-supplied keys are untrusted, so ``..`` traversal and absolute paths must
never let a read or write escape the adapter's storage sandbox.
"""

from __future__ import annotations

import os

import pytest

from perceivo.local.adapters._safe_path import PathTraversalError, safe_path
from perceivo.local.adapters.files import LocalFilesService
from perceivo.local.adapters.org_files import LocalOrgFilesService
from perceivo.local.mocks.files import MockOrgFilesService

# ---------------------------------------------------------------------------
# safe_path primitive
# ---------------------------------------------------------------------------


def test_safe_path_allows_nested_relative(tmp_path):
    resolved = safe_path(tmp_path, "documents/report.txt")
    assert str(resolved).startswith(str(tmp_path.resolve()) + os.sep)


def test_safe_path_skips_empty_and_none_segments(tmp_path):
    assert safe_path(tmp_path, None, "", "a.txt") == (tmp_path.resolve() / "a.txt")


@pytest.mark.parametrize("evil", ["../escape.txt", "a/../../escape.txt", "x/../../../y"])
def test_safe_path_rejects_parent_traversal(tmp_path, evil):
    with pytest.raises(PathTraversalError):
        safe_path(tmp_path, evil)


@pytest.mark.parametrize("evil", ["/etc/passwd", "\\\\server\\share", "/abs"])
def test_safe_path_rejects_absolute_segments(tmp_path, evil):
    with pytest.raises(PathTraversalError):
        safe_path(tmp_path, evil)


# ---------------------------------------------------------------------------
# LocalFilesService
# ---------------------------------------------------------------------------


async def test_local_files_write_read_roundtrip(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "work"))
    await svc.write("hello", "notes/a.txt")
    assert await svc.read("notes/a.txt") == "hello"


async def test_local_files_read_rejects_traversal(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    svc = LocalFilesService(working_dir=str(tmp_path / "work"))
    with pytest.raises(PathTraversalError):
        await svc.read("../secret.txt")


async def test_local_files_write_rejects_traversal(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "work"))
    with pytest.raises(PathTraversalError):
        await svc.write("pwned", "../../escape.txt")
    assert not (tmp_path / "escape.txt").exists()


# ---------------------------------------------------------------------------
# LocalOrgFilesService
# ---------------------------------------------------------------------------


async def test_org_files_read_rejects_traversal(tmp_path):
    (tmp_path / "outside.txt").write_text("nope", encoding="utf-8")
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    with pytest.raises(PathTraversalError):
        await svc.read("../outside.txt")


async def test_org_files_write_rejects_traversal(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    with pytest.raises(PathTraversalError):
        await svc.write("evil.txt", "data", parent_id="../..")
    assert not (tmp_path / "evil.txt").exists()


async def test_org_files_rename_rejects_traversal(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    await svc.write("a.txt", "x")
    with pytest.raises(PathTraversalError):
        await svc.rename("a.txt", "../../escaped.txt")
    assert not (tmp_path / "escaped.txt").exists()


async def test_org_files_move_rejects_traversal(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    await svc.write("a.txt", "x")
    with pytest.raises(PathTraversalError):
        await svc.move("a.txt", "../..")


# ---------------------------------------------------------------------------
# MockOrgFilesService seed writing
# ---------------------------------------------------------------------------


def test_mock_seed_skips_traversal_keys(tmp_path):
    org = tmp_path / "org"
    seed = {
        "good/policy.txt": "kept",
        "../../escape.txt": "must-not-be-written",
    }
    MockOrgFilesService(str(org), seed)
    assert (org / "good" / "policy.txt").read_text(encoding="utf-8") == "kept"
    # The traversal key was skipped, not written outside the sandbox root.
    assert not (tmp_path / "escape.txt").exists()


async def test_mock_seed_missing_read_returns_default(tmp_path):
    svc = MockOrgFilesService(str(tmp_path / "org"), {"doc.txt": "seeded"})
    assert await svc.read_text("doc.txt") == "seeded"
    # Unmapped read returns canned content instead of raising.
    assert "mock" in (await svc.read_text("nonexistent.txt")).lower()
