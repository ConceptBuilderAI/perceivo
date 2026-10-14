"""The declared coverage floor is the floor public CI actually enforces.

`pyproject.toml` declares `--cov-fail-under=95` in `[tool.pytest.ini_options]`
addopts. A command-line `--cov-fail-under` or an addopts override wins over that
declaration, so public CI must use the repository setting without weakening it.

These tests are the positive assertion that repair is pinned by: they fail if the
override comes back, if the declaration is deleted, or if the two drift apart.  A
green CI run is not evidence here -- a disarmed gate is green by construction.
"""

import re
import tomllib
from pathlib import Path
from typing import TypedDict

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The step in ``ci.yml`` that runs the root package's own test tree.
ROOT_TEST_STEP_NAMES = {
    "Run public unit suite at the declared coverage floor",
    "Run tests with coverage",
}

_FAIL_UNDER = re.compile(r"--cov-fail-under[= ](\d+)")


class _RootTestStep(TypedDict):
    run: str


def _declared_floor() -> int:
    """The floor the repository declares, read from ``pyproject.toml``."""
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    addopts = metadata["tool"]["pytest"]["ini_options"]["addopts"]
    match = _FAIL_UNDER.search(addopts)
    assert match is not None, (
        "pyproject.toml [tool.pytest.ini_options] addopts no longer declares "
        f"--cov-fail-under; addopts is {addopts!r}. The floor this test exists "
        "to protect has been deleted rather than moved."
    )
    return int(match.group(1))


def _root_test_step() -> _RootTestStep:
    """The ``ci.yml`` step that runs the root test tree, located by name."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    job_name = "test" if "test" in workflow["jobs"] else "lint-test"
    steps = workflow["jobs"][job_name]["steps"]
    matching = [step for step in steps if step.get("name") in ROOT_TEST_STEP_NAMES]
    assert len(matching) == 1, (
        f"expected exactly one root test step named in {sorted(ROOT_TEST_STEP_NAMES)!r} "
        f"in the {job_name} job of {CI_WORKFLOW.name}, found {len(matching)}"
    )
    run = matching[0].get("run")
    assert isinstance(run, str), f"the root test step has no string run command: {matching[0]!r}"
    return {"run": run}


def test_pyproject_declares_a_coverage_floor():
    """The declaration exists and is a real floor, not zero."""
    assert _declared_floor() >= 95


def test_ci_does_not_override_the_declared_coverage_floor():
    """CI must not weaken the declared floor on the command line.

    The root step deliberately passes no ``--cov-fail-under`` and no
    ``-o addopts=""``, so ``pyproject.toml`` is the single source of the number.
    An explicit flag at or above the declared floor is tolerated; anything below
    it -- ``0`` most of all -- is the defect this test was written for.
    """
    run = _root_test_step()["run"]
    assert "pytest" in run, f"the root test step no longer runs pytest: {run!r}"
    assert "addopts" not in run, (
        f"the root test step overrides addopts ({run!r}), which "
        "discards the declared floor without naming a replacement."
    )

    override = _FAIL_UNDER.search(run)
    if override is not None:
        declared = _declared_floor()
        assert int(override.group(1)) >= declared, (
            f"{CI_WORKFLOW.name}'s root test step passes "
            f"--cov-fail-under={override.group(1)}, below the {declared} that "
            "pyproject.toml declares. A command-line --cov-fail-under wins over "
            "addopts, so this silently disarms the repository's own gate."
        )
