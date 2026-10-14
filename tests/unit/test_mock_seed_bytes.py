"""MockOrgFilesService seeds files byte-exact.

``Path.write_text(data, encoding="utf-8")`` translates every ``\\n`` to
``os.linesep``. A seed written that way on Windows lands on disk with a CR
before every LF, ``read_text()`` returns those CRs to the caller, and the seeded
file differs from the same content written through ``write_text()`` (which
encodes and writes bytes).

⛔ WHICH TESTS CAN FAIL WHERE. The three behavioural tests below are
Windows-discriminating only: on Linux ``write_text`` translates nothing, so they
pass whatever the seed path does and a green Linux run verifies none of them. The
control, ``test_seed_path_does_not_use_text_mode``, is the one that fails on
every platform when the seed path routes through newline-translating text mode.
"""

import os
import pathlib

import pytest

from perceivo.local.mocks.files import MockOrgFilesService

# 25 LF-terminated lines: enough that one CR per line is unmistakable.
SEED = "".join(f"policy line {i:02d}: employees must follow rule {i}\n" for i in range(25))

windows_discriminating = pytest.mark.skipif(
    os.linesep == "\n",
    reason="Windows-discriminating: vacuously green where os.linesep is LF",
)


def _seeded(tmp_path):
    org = tmp_path / "org"
    return org, MockOrgFilesService(str(org), {"docs/policy.txt": SEED})


def test_seed_path_does_not_use_text_mode(tmp_path, monkeypatch):
    """AC4 control: fails on ANY platform if seeding goes through write_text."""

    def refuse(self, *args, **kwargs):
        raise AssertionError("seed path used newline-translating Path.write_text")

    monkeypatch.setattr(pathlib.Path, "write_text", refuse)
    org, _ = _seeded(tmp_path)
    assert (org / "docs" / "policy.txt").read_bytes() == SEED.encode("utf-8")


@windows_discriminating
def test_seed_file_is_byte_exact(tmp_path):
    """AC1 (Windows-discriminating)."""
    org, _ = _seeded(tmp_path)
    assert (org / "docs" / "policy.txt").read_bytes() == SEED.encode("utf-8")


@windows_discriminating
async def test_read_text_returns_the_seed(tmp_path):
    """AC2 (Windows-discriminating)."""
    _, svc = _seeded(tmp_path)
    assert await svc.read_text("docs/policy.txt") == SEED


@windows_discriminating
async def test_seeded_and_written_files_agree(tmp_path):
    """AC3 (Windows-discriminating): same content, same bytes, same size."""
    org, svc = _seeded(tmp_path)
    written = await svc.write_text("written.txt", SEED, parent_id="docs")

    seeded_entry = next(e for e in await svc.list("docs") if e["name"] == "policy.txt")
    assert seeded_entry["size_bytes"] == written["version"]["size_bytes"]
    assert (await svc.find("policy.txt"))["size_bytes"] == written["version"]["size_bytes"]
    assert (org / "docs" / "policy.txt").read_bytes() == (org / "docs" / "written.txt").read_bytes()
