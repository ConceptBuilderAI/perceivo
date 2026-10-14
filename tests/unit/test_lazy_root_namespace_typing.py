"""Static discovery contract for the lazy modules on the root namespace."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import perceivo

# One real public member makes each positive probe useful. The key-set assertion
# below forces this evidence to grow whenever the runtime lazy plane grows.
_POSITIVE_MEMBERS = {
    "bootstrap": "session",
    "component": "create",
    "config": "load",
    "local": "mode",
    "orchestration": "create",
    "runner": "run_orchestration",
    "templates": "use",
    "validator": "validate_component",
}


def _run_mypy(probe: Path, cache_dir: Path) -> subprocess.CompletedProcess[str]:
    assert importlib.util.find_spec("mypy") is not None, (
        "mypy is required to verify the public typing contract; install the development extra"
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--follow-imports=silent",
            "--show-error-codes",
            f"--cache-dir={cache_dir}",
            str(probe),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )


def test_static_tools_resolve_real_members_on_every_lazy_module(tmp_path: Path) -> None:
    lazy_modules = set(perceivo._LOCAL_PLANE)
    assert set(_POSITIVE_MEMBERS) == lazy_modules

    statements = [
        "import perceivo",
        *(
            f"member_{module} = perceivo.{module}.{_POSITIVE_MEMBERS[module]}"
            for module in sorted(lazy_modules)
        ),
    ]
    probe = tmp_path / "positive_lazy_root_namespace.py"
    probe.write_text("\n".join(statements) + "\n", encoding="utf-8")

    result = _run_mypy(probe, tmp_path / "mypy-positive-cache")

    assert result.returncode == 0, result.stdout + result.stderr


def test_static_tools_reject_unknown_members_on_every_lazy_module(tmp_path: Path) -> None:
    lazy_modules = sorted(perceivo._LOCAL_PLANE)
    statements = [
        "import perceivo",
        *(f"perceivo.{module}.__fh001_missing_{module}()" for module in lazy_modules),
    ]
    probe = tmp_path / "negative_lazy_root_namespace.py"
    probe.write_text("\n".join(statements) + "\n", encoding="utf-8")

    result = _run_mypy(probe, tmp_path / "mypy-negative-cache")
    output = result.stdout + result.stderr
    diagnostics = [line for line in output.splitlines() if "[attr-defined]" in line]

    assert result.returncode == 1, output
    assert len(diagnostics) == len(lazy_modules), output
    for module in lazy_modules:
        marker = f"__fh001_missing_{module}"
        assert sum(marker in diagnostic for diagnostic in diagnostics) == 1, output
