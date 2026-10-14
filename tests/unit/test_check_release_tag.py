"""Must-fail and positive fixtures for ``scripts/check_release_tag.py``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_release_tag as release_tag
from scripts.check_release_tag import TagRejected, check_release_tag, main, version_from_tag


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _repository(root: Path, version: str = "1.0.0rc1", pyproject: str | None = None) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "release-check@example.invalid")
    _git(root, "config", "user.name", "release check")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "perceivo"\nversion = "{pyproject or version}"\n', encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "release")
    return _git(root, "rev-parse", "HEAD")


def test_accepts_rc_tag_matching_version_and_pyproject(tmp_path: Path) -> None:
    commit = _repository(tmp_path)
    _git(tmp_path, "tag", "v1.0.0rc1", commit)
    assert check_release_tag(tmp_path, "v1.0.0rc1", commit, "main") == "1.0.0rc1"


def test_accepts_final_tag(tmp_path: Path) -> None:
    commit = _repository(tmp_path, "1.0.0")
    _git(tmp_path, "tag", "v1.0.0", commit)
    assert (
        main(["--root", str(tmp_path), "--tag", "v1.0.0", "--commit", commit, "--main-ref", "main"])
        == 0
    )


@pytest.mark.parametrize(
    "tag",
    [
        "v1.0.0-rc.1",
        "v1.0.0.rc1",
        "v01.0.0",
        "1.0.0rc1",
        "v1.0",
        "v1.0.0rc",
        "v1.0.0rc01",
        "V1.0.0rc1",
    ],
)
def test_refuses_tags_not_in_normal_form(tag: str) -> None:
    with pytest.raises(TagRejected):
        version_from_tag(tag)


def test_refuses_version_file_mismatch(tmp_path: Path) -> None:
    commit = _repository(tmp_path, "1.0.0rc2", pyproject="1.0.0rc1")
    _git(tmp_path, "tag", "v1.0.0rc1", commit)
    with pytest.raises(TagRejected, match="VERSION"):
        check_release_tag(tmp_path, "v1.0.0rc1", commit, "main")


def test_refuses_pyproject_mismatch(tmp_path: Path) -> None:
    commit = _repository(tmp_path, "1.0.0rc1", pyproject="1.0.0rc2")
    _git(tmp_path, "tag", "v1.0.0rc1", commit)
    with pytest.raises(TagRejected, match="pyproject"):
        check_release_tag(tmp_path, "v1.0.0rc1", commit, "main")


def test_refuses_commit_not_on_main(tmp_path: Path) -> None:
    _repository(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "side")
    (tmp_path / "extra.txt").write_text("side\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "side")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "tag", "v1.0.0rc1", commit)
    with pytest.raises(TagRejected, match="not an ancestor"):
        check_release_tag(tmp_path, "v1.0.0rc1", commit, "main")


def test_refuses_tag_pointing_elsewhere(tmp_path: Path) -> None:
    first = _repository(tmp_path)
    _git(tmp_path, "tag", "v1.0.0rc1", first)
    (tmp_path / "extra.txt").write_text("next\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "next")
    second = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(TagRejected, match="points at"):
        check_release_tag(tmp_path, "v1.0.0rc1", second, "main")


def test_refuses_short_commit(tmp_path: Path) -> None:
    commit = _repository(tmp_path)
    _git(tmp_path, "tag", "v1.0.0rc1", commit)
    with pytest.raises(TagRejected, match="not a full 40-hex SHA"):
        check_release_tag(tmp_path, "v1.0.0rc1", commit[:12], "main")


def test_refuses_missing_tag(tmp_path: Path) -> None:
    commit = _repository(tmp_path)
    with pytest.raises(TagRejected, match="cannot resolve"):
        check_release_tag(tmp_path, "v1.0.0rc1", commit, "main")


INJECTIONS = [
    "--output=/tmp/x",
    "-x",
    "main;id",
    "$(id)",
    "`id`",
    "main..HEAD",
    "../main",
    "main main",
    "",
]


@pytest.mark.parametrize("main_ref", INJECTIONS)
def test_main_ref_injection_is_refused_before_git(
    tmp_path: Path, main_ref: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = _repository(tmp_path)
    _git(tmp_path, "tag", "v1.0.0rc1", commit)
    with pytest.raises(TagRejected, match="not a plain reference name"):
        check_release_tag(tmp_path, "v1.0.0rc1", commit, main_ref)


@pytest.mark.parametrize("tag", ["v1.0.0rc1;id", "--output=/tmp/x", "-x", "v$(id)", "v1.0.0rc1 -x"])
def test_tag_injection_is_refused(tag: str) -> None:
    with pytest.raises(TagRejected):
        version_from_tag(tag)


@pytest.mark.parametrize("operand", ["--output=/tmp/x", "-x", "a;b", "$(id)", "a..b", "a b"])
def test_git_refuses_unsafe_operand_without_running(
    operand: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("git was invoked")

    monkeypatch.setattr(release_tag.subprocess, "run", forbidden)
    here = Path(".")
    with pytest.raises(TagRejected, match="refusing to pass"):
        release_tag._git(here, "rev-parse", ("--verify",), (operand,))


def test_git_ends_options_before_operands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def record(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(release_tag.subprocess, "run", record)
    release_tag._git(Path("."), "merge-base", ("--is-ancestor",), ("a" * 40, "b" * 40))
    assert seen == [["git", "merge-base", "--is-ancestor", "--end-of-options", "a" * 40, "b" * 40]]
