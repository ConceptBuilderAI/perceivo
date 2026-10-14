"""Distribution metadata regression tests."""

import tomllib
from pathlib import Path


def test_dev_extra_installs_full_test_suite_dependencies():
    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    dev_dependencies = metadata["project"]["optional-dependencies"]["dev"]

    assert any(dependency.startswith("fastapi") for dependency in dev_dependencies)
