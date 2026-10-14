#!/usr/bin/env python3
"""Enforce the release-workflow policy over ``.github/workflows``.

Every workflow file:

* pins every remote ``uses:`` to a full 40-hex commit SHA;
* has no ``pull_request_target`` trigger;
* grants ``id-token: write`` only to the publish workflow's ``testpypi`` and
  ``pypi`` jobs, and ``contents: write`` only to its ``github-release`` job;
* never grants ``write-all``.

Every file in the publish workflow's transitive closure over local
``uses: ./...`` references (called workflows and local actions):

* has no ``actions/cache`` step and no ``cache`` key in any step's ``with:``;
* sets ``persist-credentials: false`` on every ``actions/checkout``;
* resolves: a local reference to a missing file is a violation.

The publish workflow additionally has a top-level ``permissions: {}`` and a
``verify`` job that checks out with ``fetch-depth: 0``.

A tag-triggered run can restore caches that default-branch runs wrote, so a
cache anywhere in the closure is a path for untrusted bytes into an upload.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml

WORKFLOW_DIR: Final = PurePosixPath(".github/workflows")
PUBLISH_WORKFLOW: Final = WORKFLOW_DIR / "release.yml"
FULL_SHA: Final = re.compile(r"[0-9a-f]{40}")
ID_TOKEN_JOBS: Final = frozenset({"testpypi", "pypi"})
CONTENTS_WRITE_JOBS: Final = frozenset({"github-release"})
CACHE_ACTION: Final = re.compile(r"^actions/cache(?:/[^@]*)?@", re.IGNORECASE)
CHECKOUT_ACTION: Final = re.compile(r"^actions/checkout@", re.IGNORECASE)
DOWNLOAD_ACTION: Final = re.compile(r"^actions/download-artifact@", re.IGNORECASE)
PUBLISH_ACTION: Final = re.compile(r"^pypa/gh-action-pypi-publish@", re.IGNORECASE)
DISTRIBUTIONS_ARTIFACT: Final = "distributions"
#: The only trusted source of distribution digests: the build job's outputs.
#: A run artifact can be replaced by any later job in the run; a job output
#: cannot. Each verifying step must map exactly these environment variables.
BUILD_OUTPUT_ENV: Final = {
    "WHEEL_NAME": "${{ needs.build.outputs.wheel_name }}",
    "WHEEL_SHA256": "${{ needs.build.outputs.wheel_sha256 }}",
    "SDIST_NAME": "${{ needs.build.outputs.sdist_name }}",
    "SDIST_SHA256": "${{ needs.build.outputs.sdist_sha256 }}",
}


@dataclass
class Report:
    """Violations found, and the population they were drawn from."""

    violations: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    closure: list[str] = field(default_factory=list)
    jobs: int = 0
    uses: int = 0

    def add(self, where: str, message: str) -> None:
        self.violations.append(f"{where}: {message}")


def _load(path: Path) -> dict[Any, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a YAML mapping")
    return value


def _triggers(workflow: dict[Any, Any]) -> Any:
    # YAML 1.1 reads a bare ``on`` key as boolean True.
    return workflow.get("on", workflow.get(True))


def _trigger_names(workflow: dict[Any, Any]) -> set[str]:
    triggers = _triggers(workflow)
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return {str(item) for item in triggers}
    if isinstance(triggers, dict):
        return {str(key) for key in triggers}
    return set()


def _jobs(workflow: dict[Any, Any]) -> dict[str, dict[Any, Any]]:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {str(name): job for name, job in jobs.items() if isinstance(job, dict)}


def _steps(job: dict[Any, Any]) -> Iterator[dict[Any, Any]]:
    steps = job.get("steps")
    if isinstance(steps, list):
        yield from (step for step in steps if isinstance(step, dict))


def _action_steps(action: dict[Any, Any]) -> Iterator[dict[Any, Any]]:
    runs = action.get("runs")
    if isinstance(runs, dict):
        yield from _steps(runs)


def _permission_grants(permissions: Any) -> dict[str, str]:
    """Normalise a ``permissions`` value to ``{scope: level}``."""
    if permissions == "write-all":
        return {"*": "write"}
    if isinstance(permissions, dict):
        return {str(scope): str(level) for scope, level in permissions.items()}
    return {}


def _is_false(value: Any) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _local_target(root: Path, reference: str, is_job: bool) -> Path:
    relative = PurePosixPath(reference[2:])
    target = root.joinpath(*relative.parts)
    if is_job:
        return target
    for name in ("action.yml", "action.yaml"):
        if (target / name).is_file():
            return target / name
    return target / "action.yml"


def _check_uses(report: Report, where: str, uses: Any) -> str | None:
    """Check one ``uses:`` value; return it if it is a local reference."""
    if not isinstance(uses, str):
        report.add(where, f"uses is not a string: {uses!r}")
        return None
    report.uses += 1
    if uses.startswith("./"):
        return uses
    if uses.startswith("docker://"):
        report.add(where, f"docker action is not SHA-pinned: {uses}")
        return None
    _, _, pin = uses.partition("@")
    if not FULL_SHA.fullmatch(pin):
        report.add(where, f"action is not pinned to a full commit SHA: {uses}")
    return None


def _check_permissions(
    report: Report, relative: str, job_name: str | None, permissions: Any
) -> None:
    grants = _permission_grants(permissions)
    where = f"{relative} job {job_name}" if job_name else f"{relative} (workflow level)"
    if grants.get("*") == "write":
        report.add(where, "permissions: write-all is forbidden")
        return
    is_publish = relative == PUBLISH_WORKFLOW.as_posix() and job_name is not None
    if grants.get("id-token") == "write" and not (is_publish and job_name in ID_TOKEN_JOBS):
        report.add(where, "id-token: write is allowed only on release.yml jobs testpypi and pypi")
    if grants.get("contents") == "write" and not (is_publish and job_name in CONTENTS_WRITE_JOBS):
        report.add(where, "contents: write is allowed only on release.yml job github-release")


def _check_step_in_closure(report: Report, where: str, step: dict[Any, Any]) -> None:
    uses = step.get("uses")
    with_block = step.get("with")
    if isinstance(uses, str) and CACHE_ACTION.match(uses):
        report.add(where, f"cache action in the publish closure: {uses}")
    if isinstance(with_block, dict) and "cache" in with_block:
        report.add(
            where, f"step sets a cache key in the publish closure: cache={with_block['cache']!r}"
        )
    if isinstance(uses, str) and CHECKOUT_ACTION.match(uses):
        persist = with_block.get("persist-credentials") if isinstance(with_block, dict) else None
        if not _is_false(persist):
            report.add(where, "actions/checkout without persist-credentials: false")


def _job_references(report: Report, where: str, job: dict[Any, Any]) -> list[tuple[str, bool]]:
    """Check every ``uses:`` of one job; return its local references."""
    local: list[tuple[str, bool]] = []
    if "uses" in job:
        reference = _check_uses(report, where, job["uses"])
        if reference:
            local.append((reference, True))
    for step in _steps(job):
        if "uses" in step:
            reference = _check_uses(report, where, step["uses"])
            if reference:
                local.append((reference, False))
    return local


def _check_workflow_file(report: Report, root: Path, relative: str) -> list[tuple[str, bool]]:
    """Apply the every-file rules; return the local references it makes."""
    workflow = _load(root / relative)
    local: list[tuple[str, bool]] = []
    if "pull_request_target" in _trigger_names(workflow):
        report.add(relative, "pull_request_target trigger is forbidden")
    _check_permissions(report, relative, None, workflow.get("permissions"))
    for job_name, job in _jobs(workflow).items():
        report.jobs += 1
        _check_permissions(report, relative, job_name, job.get("permissions"))
        local += _job_references(report, f"{relative} job {job_name}", job)
    return local


def _local_uses(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("./")


def _action_closure_rules(
    report: Report, relative: str, document: dict[Any, Any]
) -> list[tuple[str, bool]]:
    where = f"{relative} (action)"
    local: list[tuple[str, bool]] = []
    for step in _action_steps(document):
        _check_step_in_closure(report, where, step)
        if "uses" in step:
            reference = _check_uses(report, where, step.get("uses"))
            if reference:
                local.append((reference, False))
    return local


def _workflow_closure_rules(
    report: Report, relative: str, document: dict[Any, Any]
) -> list[tuple[str, bool]]:
    local: list[tuple[str, bool]] = []
    for job_name, job in _jobs(document).items():
        where = f"{relative} job {job_name}"
        for step in _steps(job):
            _check_step_in_closure(report, where, step)
        if _local_uses(job.get("uses")):
            local.append((job["uses"], True))
        local += [(step["uses"], False) for step in _steps(job) if _local_uses(step.get("uses"))]
    return local


def _closure_rules(
    report: Report, root: Path, relative: str, is_action: bool
) -> list[tuple[str, bool]]:
    """Apply the closure-only rules to one file; return its local references."""
    document = _load(root / relative)
    if is_action:
        return _action_closure_rules(report, relative, document)
    return _workflow_closure_rules(report, relative, document)


def _uses(step: dict[Any, Any], pattern: re.Pattern[str]) -> bool:
    return isinstance(step.get("uses"), str) and bool(pattern.match(step["uses"]))


def _reads_build_outputs(step: dict[Any, Any]) -> bool:
    env = step.get("env")
    if not isinstance(env, dict):
        return False
    return all(
        " ".join(str(env.get(name, "")).split()) == value
        for name, value in BUILD_OUTPUT_ENV.items()
    )


def _is_verify_step(step: dict[Any, Any]) -> bool:
    run = step.get("run")
    return isinstance(run, str) and "sha256sum --strict -c" in run and _reads_build_outputs(step)


def _needs(job: dict[Any, Any]) -> set[str]:
    needs = job.get("needs")
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return {str(item) for item in needs}
    return set()


def _check_download(report: Report, where: str, steps: list[dict[Any, Any]], index: int) -> None:
    """A download must be 'distributions', immediately followed by verification."""
    with_block = steps[index].get("with")
    artifact = with_block.get("name") if isinstance(with_block, dict) else None
    if artifact != DISTRIBUTIONS_ARTIFACT:
        report.add(
            where,
            f"downloads artifact {artifact!r}: digests must come from "
            "needs.build.outputs, and only 'distributions' may be downloaded",
        )
        return
    following = steps[index + 1] if index + 1 < len(steps) else {}
    if not _is_verify_step(following):
        report.add(
            where,
            "the step after downloading 'distributions' must verify them with "
            "sha256sum --strict -c against needs.build.outputs",
        )


def _check_index_step(report: Report, where: str, step: dict[Any, Any]) -> None:
    run = step.get("run")
    if not (isinstance(run, str) and "compare_index_artifacts.py index" in run):
        return
    if "--expect" not in run or not _reads_build_outputs(step):
        report.add(where, "index comparison must --expect needs.build.outputs")


def _check_job_digest_source(report: Report, where: str, job: dict[Any, Any]) -> None:
    steps = list(_steps(job))
    verified_at: int | None = None
    for index, step in enumerate(steps):
        if _uses(step, DOWNLOAD_ACTION):
            _check_download(report, where, steps, index)
        if _is_verify_step(step) and verified_at is None:
            verified_at = index
        if _uses(step, PUBLISH_ACTION) and verified_at is None:
            report.add(where, "uploads before verifying against needs.build.outputs")
        _check_index_step(report, where, step)
    reads_outputs = any(_reads_build_outputs(step) for step in steps)
    if reads_outputs and "build" not in _needs(job):
        report.add(where, "reads needs.build.outputs but does not need build")


def _check_digest_source(report: Report, relative: str, workflow: dict[Any, Any]) -> None:
    """Distribution bytes are proven against the build job's outputs, never an artifact."""
    for job_name, job in _jobs(workflow).items():
        _check_job_digest_source(report, f"{relative} job {job_name}", job)


def _check_publish_shape(report: Report, root: Path) -> None:
    relative = PUBLISH_WORKFLOW.as_posix()
    workflow = _load(root / relative)
    _check_digest_source(report, relative, workflow)
    if workflow.get("permissions") != {}:
        report.add(relative, "top-level permissions must be {}")
    verify = _jobs(workflow).get("verify")
    if verify is None:
        report.add(relative, "job verify is missing")
        return
    checkouts = [
        step
        for step in _steps(verify)
        if isinstance(step.get("uses"), str) and CHECKOUT_ACTION.match(step["uses"])
    ]
    if not checkouts:
        report.add(f"{relative} job verify", "verify does not check out the repository")
    for step in checkouts:
        with_block = step.get("with")
        depth = with_block.get("fetch-depth") if isinstance(with_block, dict) else None
        if str(depth) != "0":
            report.add(f"{relative} job verify", "checkout must set fetch-depth: 0")


def _workflow_files(root: Path) -> list[Path]:
    directory = root.joinpath(*WORKFLOW_DIR.parts)
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.iterdir() if path.suffix in {".yml", ".yaml"} and path.is_file()
    )


def _walk_closure(report: Report, root: Path) -> None:
    pending: list[tuple[str, bool]] = [(PUBLISH_WORKFLOW.as_posix(), False)]
    seen: set[str] = set()
    while pending:
        relative, is_action = pending.pop(0)
        if relative in seen:
            continue
        seen.add(relative)
        report.closure.append(relative)
        if not is_action and relative not in report.files:
            # A called workflow outside .github/workflows still gets every-file rules.
            _check_workflow_file(report, root, relative)
        for reference, is_job in _closure_rules(report, root, relative, is_action):
            target = _local_target(root, reference, is_job)
            if not target.is_file():
                report.add(relative, f"unresolvable local reference: {reference}")
                continue
            pending.append((target.relative_to(root).as_posix(), not is_job))


def check(root: Path) -> Report:
    report = Report()
    for path in _workflow_files(root):
        relative = path.relative_to(root).as_posix()
        report.files.append(relative)
        _check_workflow_file(report, root, relative)
    if not report.files:
        report.add(WORKFLOW_DIR.as_posix(), "no workflow files found")
        return report
    if not root.joinpath(*PUBLISH_WORKFLOW.parts).is_file():
        report.add(PUBLISH_WORKFLOW.as_posix(), "publish workflow is missing")
        return report
    _check_publish_shape(report, root)
    _walk_closure(report, root)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        report = check(root)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"workflow policy rejected: {exc}", file=sys.stderr)
        return 1
    names = ", ".join(PurePosixPath(item).name for item in report.closure)
    print(f"closure: {names}")
    print(f"population: files {len(report.files)}, jobs {report.jobs}, uses {report.uses}")
    for violation in report.violations:
        print(f"violation: {violation}", file=sys.stderr)
    if report.violations:
        return 1
    if report.jobs == 0 or report.uses == 0:
        print("workflow policy rejected: empty population", file=sys.stderr)
        return 1
    print("workflow policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
