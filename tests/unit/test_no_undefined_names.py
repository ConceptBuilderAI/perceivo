"""Static guard: every name referenced under ``perceivo/`` is defined or imported.

Mock-driven tests don't execute the function bodies that touch real I/O, so a
missing import inside one of those bodies can stay hidden until production
traffic hits it. Pyflakes performs lexical-scope analysis on function bodies
and surfaces undefined-name references statically.

This is the cheap regression layer that closes the gap created by the Phase D
decomposition: methods get moved into mixin modules, and their imports must
travel with them.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "perceivo"


def test_no_undefined_names_in_sdk():
    assert TARGET.is_dir(), "perceivo package directory not found"
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(TARGET)],
        capture_output=True,
        text=True,
        check=False,
    )
    undefined = [
        line
        for line in result.stdout.splitlines()
        if "undefined name" in line or "may be undefined" in line
    ]
    assert not undefined, (
        "pyflakes found names referenced but never defined or imported. "
        "This usually means a method was extracted into a mixin/sibling "
        "module without bringing its imports along.\n\n" + "\n".join(undefined)
    )
