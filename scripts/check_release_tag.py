#!/usr/bin/env python3
"""Refuse a release tag that does not name exactly what this commit would ship.

A release tag is accepted only when all of these hold:

* it is ``v`` followed by a version in PEP 440 normal form, restricted to the
  shapes this project releases: ``X.Y.Z`` or ``X.Y.ZrcN``, with no leading
  zeros (so ``v1.0.0-rc.1``, ``v1.0.0.rc1`` and ``v01.0.0`` are refused);
* the version equals ``VERSION`` and ``[project].version`` in ``pyproject.toml``;
* the tag resolves to the commit being released; and
* that commit is an ancestor of the main branch reference given.

This is a mistake guard, not a security control: the workflow that runs it is
read from the tagged commit itself. The controls that gate an upload are the
tag ruleset, the ``pypi`` environment's reviewer and the publisher binding.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

#: ``X.Y.Z`` or ``X.Y.ZrcN``; every number is ``0`` or has no leading zero.
_NUMBER: Final = r"(?:0|[1-9][0-9]*)"
RELEASE_VERSION: Final = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:rc{_NUMBER})?")
FULL_SHA: Final = re.compile(r"[0-9a-f]{40}")
#: A branch or remote-tracking reference name for ``--main-ref``: path segments of
#: letters, digits, ``_``, ``.`` and ``-``, each starting with a letter or digit.
REF_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*")
#: Every operand handed to git: never an option, never shell or revision syntax
#: beyond the ``^{commit}`` peel this script appends itself.
GIT_OPERAND: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*(?:\^\{commit\})?")


class TagRejected(Exception):
    """The tag must not be released."""


def version_from_tag(tag: str) -> str:
    """Return the version a tag names, or raise if the tag is not normal form."""
    if not tag.startswith("v"):
        raise TagRejected(f"tag {tag!r} does not start with 'v'")
    version = tag[1:]
    if not RELEASE_VERSION.fullmatch(version):
        raise TagRejected(f"tag {tag!r} is not v<X.Y.Z> or v<X.Y.Z>rc<N> in PEP 440 normal form")
    return version


def declared_versions(root: Path) -> tuple[str, str]:
    """Return ``(VERSION, pyproject version)`` as written in the tree."""
    version_file = (root / "VERSION").read_text(encoding="utf-8").strip()
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle).get("project", {})
    pyproject_version = project.get("version")
    if not isinstance(pyproject_version, str):
        raise TagRejected("pyproject.toml has no static [project].version")
    return version_file, pyproject_version


def _git(
    root: Path, subcommand: str, options: tuple[str, ...], operands: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    """Run git with fixed options and validated operands after ``--end-of-options``."""
    safe_operands: list[str] = []
    for operand in operands:
        match = GIT_OPERAND.fullmatch(operand)
        if match is None or operand.startswith("-") or ".." in operand:
            raise TagRejected(f"refusing to pass {operand!r} to git")
        safe_operands.append(match.group(0))
    return subprocess.run(
        ["git", subcommand, *options, "--end-of-options", *safe_operands],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_commit(root: Path, revision: str) -> str:
    completed = _git(root, "rev-parse", ("--verify", "--quiet"), (f"{revision}^{{commit}}",))
    sha = completed.stdout.strip()
    if completed.returncode or not FULL_SHA.fullmatch(sha):
        raise TagRejected(f"cannot resolve {revision!r} to a commit")
    return sha


def check_release_tag(root: Path, tag: str, commit: str, main_ref: str) -> str:
    """Validate ``tag`` for ``commit``; return the version on success."""
    version = version_from_tag(tag)
    version_file, pyproject_version = declared_versions(root)
    if version_file != version:
        raise TagRejected(f"VERSION is {version_file!r}, tag names {version!r}")
    if pyproject_version != version:
        raise TagRejected(f"pyproject.toml version is {pyproject_version!r}, tag names {version!r}")
    if not FULL_SHA.fullmatch(commit):
        raise TagRejected(f"commit {commit!r} is not a full 40-hex SHA")
    if not REF_NAME.fullmatch(main_ref) or ".." in main_ref:
        raise TagRejected(f"main ref {main_ref!r} is not a plain reference name")
    head = _resolve_commit(root, "HEAD")
    if head != commit:
        raise TagRejected(f"checked-out HEAD {head} is not the release commit {commit}")
    tagged = _resolve_commit(root, f"refs/tags/{tag}")
    if tagged != commit:
        raise TagRejected(f"tag {tag} points at {tagged}, not the release commit {commit}")
    main = _resolve_commit(root, main_ref)
    if _git(root, "merge-base", ("--is-ancestor",), (commit, main)).returncode != 0:
        raise TagRejected(f"commit {commit} is not an ancestor of {main_ref} ({main})")
    return version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--tag", required=True, help="the tag name, for example v1.0.0rc1")
    parser.add_argument("--commit", required=True, help="the full SHA being released")
    parser.add_argument("--main-ref", default="origin/main")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        version = check_release_tag(args.root, args.tag, args.commit, args.main_ref)
    except (TagRejected, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        print(f"release tag rejected: {exc}", file=sys.stderr)
        return 1
    print(f"release tag accepted: {args.tag} -> version {version} at {args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
