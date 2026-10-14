#!/usr/bin/env python3
"""Run the README's marked quick start against an installed ``perceivo``.

The quick start is the block between ``<!-- quickstart:begin -->`` and
``<!-- quickstart:end -->`` in ``README.md``: one fenced shell block of
``perceivo ...`` commands. Each command runs, in order, in one empty temporary
directory, with ``perceivo`` replaced by ``--perceivo`` (the CLI of the wheel
under test). A command that exits non-zero fails the run.

A ``# expect: TEXT`` line after a command asserts that the command's standard
output contains ``TEXT``. Only ``perceivo`` commands are allowed: this is a
check of the documented CLI, not a shell.

``--readme`` must lie inside the directory the runner is started from: the
path is canonicalised and anything outside that directory is refused.

Exit 1 when the README is outside the invoking directory, when the block is
missing, appears more than once, holds no command, holds a command that is not
``perceivo ...``, or when any command or expectation fails.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

BEGIN: Final = "<!-- quickstart:begin -->"
END: Final = "<!-- quickstart:end -->"
FENCE: Final = re.compile(r"^```[a-z]*\s*$")
TIMEOUT_SECONDS: Final = 300


class QuickstartError(Exception):
    """The quick start is missing, malformed, or did not do what it says."""


@dataclass
class Step:
    command: list[str]
    expects: list[str] = field(default_factory=list)


def marked_block(readme: str) -> str:
    if readme.count(BEGIN) != 1 or readme.count(END) != 1:
        raise QuickstartError(f"README must hold exactly one {BEGIN} ... {END} block")
    start = readme.index(BEGIN) + len(BEGIN)
    end = readme.index(END)
    if end < start:
        raise QuickstartError("quick-start end marker comes before its begin marker")
    return readme[start:end]


def steps(block: str) -> list[Step]:
    """The commands inside the block's one fenced code block, with their expectations."""
    lines = block.splitlines()
    fences = [index for index, line in enumerate(lines) if FENCE.match(line.strip())]
    if len(fences) != 2:
        raise QuickstartError("the quick-start block must hold exactly one fenced code block")
    parsed: list[Step] = []
    for raw in lines[fences[0] + 1 : fences[1]]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# expect:"):
            if not parsed:
                raise QuickstartError("an expectation comes before any command")
            parsed[-1].expects.append(line.removeprefix("# expect:").strip())
            continue
        if line.startswith("#"):
            continue
        command = shlex.split(line.removeprefix("$ "))
        if not command or command[0] != "perceivo":
            raise QuickstartError(f"only perceivo commands may appear in the quick start: {line!r}")
        parsed.append(Step(command))
    if not parsed:
        raise QuickstartError("the quick-start block holds no command")
    return parsed


def contained_readme(readme_path: Path | str) -> str:
    """``readme_path`` resolved, when it lies inside the directory the run started in.

    The README is read from, and a line of it can be echoed in an error, so the
    path is canonicalised (``..`` and symbolic links resolved) and refused unless
    it is under the invoking directory: ``--readme ../../etc/passwd`` is not a
    README this runner will open.
    """
    resolved = os.path.realpath(readme_path)
    base_dir = os.path.realpath(os.getcwd())
    if resolved != base_dir and not resolved.startswith(base_dir + os.sep):
        raise QuickstartError(f"--readme {str(readme_path)!r} is outside {base_dir}")
    return resolved


def run(readme_path: Path | str, cli: list[str]) -> list[str]:
    with open(contained_readme(readme_path), encoding="utf-8") as readme:
        plan = steps(marked_block(readme.read()))
    transcript: list[str] = []
    with tempfile.TemporaryDirectory(prefix="perceivo-quickstart-") as workdir:
        for step in plan:
            argv = [*cli, *step.command[1:]]
            completed = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            shown = " ".join(step.command)
            transcript.append(f"$ {shown}  -> rc={completed.returncode}")
            if completed.returncode:
                detail = (completed.stderr or completed.stdout).strip()[-800:]
                raise QuickstartError(f"{shown!r} exited {completed.returncode}: {detail}")
            for expected in step.expects:
                if expected not in completed.stdout:
                    raise QuickstartError(f"{shown!r} did not print {expected!r}")
    return transcript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--readme",
        type=Path,
        default=Path("README.md"),
        help="the README to run, inside the current directory",
    )
    parser.add_argument(
        "--perceivo",
        required=True,
        help="the CLI under test, for example the venv's perceivo executable",
    )
    args = parser.parse_args(argv)
    try:
        transcript = run(args.readme, shlex.split(args.perceivo, posix=True))
    except (QuickstartError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"quick start failed: {exc}", file=sys.stderr)
        return 1
    for line in transcript:
        print(line)
    print(f"quick start passed: {len(transcript)} commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
