"""The release-workflow policy, over the real workflows and planted violations."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.check_workflow_policy import check, main

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = Path(".github") / "workflows"
BUILD = WORKFLOWS / "build-distributions.yml"
PUBLISH = WORKFLOWS / "release.yml"
CI = WORKFLOWS / "ci.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / WORKFLOWS, tmp_path / WORKFLOWS)
    return tmp_path


def _plant(root: Path, relative: Path, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"plant anchor not found in {relative}: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _violations(root: Path) -> list[str]:
    return check(root).violations


def test_real_workflows_pass_with_two_file_closure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--root", str(ROOT)]) == 0
    out = capsys.readouterr().out
    assert "closure: release.yml, build-distributions.yml" in out
    report = check(ROOT)
    assert report.jobs > 0
    assert report.uses > 0
    assert len(report.files) >= 3


def test_ci_pip_cache_is_out_of_scope() -> None:
    # quality/test keep cache: pip; they are outside release.yml's closure.
    assert "cache: pip" in (ROOT / CI).read_text(encoding="utf-8")
    assert _violations(ROOT) == []


def test_planted_cache_key_in_build_fails(tree: Path) -> None:
    _plant(
        tree,
        BUILD,
        '          python-version: "3.12.14"\n',
        '          python-version: "3.12.14"\n          cache: pip\n',
    )
    found = _violations(tree)
    assert any("build-distributions.yml job build" in item and "cache" in item for item in found)


def test_planted_cache_action_in_build_fails(tree: Path) -> None:
    _plant(
        tree,
        BUILD,
        "      - name: Confirm the canonical interpreter\n",
        "      - uses: actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809 # v4\n"
        "        with:\n          path: ~/.cache/pip\n          key: pip\n"
        "      - name: Confirm the canonical interpreter\n",
    )
    assert any("cache action" in item for item in _violations(tree))


def test_planted_checkout_persisting_credentials_in_build_fails(tree: Path) -> None:
    _plant(tree, BUILD, "          persist-credentials: false\n", "")
    found = _violations(tree)
    assert any(
        "build-distributions.yml job build" in item and "persist-credentials" in item
        for item in found
    )


def test_planted_persist_true_in_publish_fails(tree: Path) -> None:
    _plant(
        tree,
        PUBLISH,
        "          persist-credentials: false\n",
        "          persist-credentials: true\n",
    )
    assert any(
        "release.yml job verify" in item and "persist-credentials" in item
        for item in _violations(tree)
    )


def test_nested_called_workflow_with_cache_fails(tree: Path) -> None:
    (tree / WORKFLOWS / "nested.yml").write_text(
        "on:\n  workflow_call:\npermissions: {}\njobs:\n  inner:\n    runs-on: ubuntu-24.04\n"
        "    steps:\n      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97\n"
        "        with:\n          cache: pip\n",
        encoding="utf-8",
    )
    _plant(tree, BUILD, "jobs:\n", "jobs:\n  nested:\n    uses: ./.github/workflows/nested.yml\n")
    report = check(tree)
    assert any(path.endswith("nested.yml") for path in report.closure)
    assert any("nested.yml job inner" in item and "cache" in item for item in report.violations)


def test_local_action_with_cache_in_closure_fails(tree: Path) -> None:
    action = tree / ".github" / "actions" / "setup"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "name: setup\nruns:\n  using: composite\n  steps:\n"
        "    - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97\n"
        "      with:\n        cache: pip\n",
        encoding="utf-8",
    )
    _plant(
        tree,
        BUILD,
        "      - name: Confirm the canonical interpreter\n",
        "      - uses: ./.github/actions/setup\n      - name: Confirm the canonical interpreter\n",
    )
    assert any("action.yml (action)" in item and "cache" in item for item in _violations(tree))


def test_missing_local_workflow_fails(tree: Path) -> None:
    (tree / BUILD).unlink()
    assert any("unresolvable local reference" in item for item in _violations(tree))


def test_id_token_at_build_workflow_level_fails(tree: Path) -> None:
    _plant(tree, BUILD, "permissions: {}\n", "permissions:\n  id-token: write\n")
    assert any(
        "build-distributions.yml (workflow level)" in item and "id-token" in item
        for item in _violations(tree)
    )


def test_id_token_on_build_job_fails(tree: Path) -> None:
    _plant(
        tree,
        BUILD,
        "    permissions:\n      contents: read\n",
        "    permissions:\n      contents: read\n      id-token: write\n",
    )
    assert any(
        "build-distributions.yml job build" in item and "id-token" in item
        for item in _violations(tree)
    )


def test_id_token_on_other_publish_job_fails(tree: Path) -> None:
    _plant(
        tree,
        PUBLISH,
        "    permissions: {}\n    strategy:",
        "    permissions:\n      id-token: write\n    strategy:",
    )
    assert any(
        "release.yml job install-matrix" in item and "id-token" in item
        for item in _violations(tree)
    )


def test_contents_write_outside_github_release_fails(tree: Path) -> None:
    _plant(
        tree,
        CI,
        "    permissions:\n      contents: read\n",
        "    permissions:\n      contents: write\n",
    )
    assert any(
        "ci.yml job build" in item and "contents: write" in item for item in _violations(tree)
    )


def test_write_all_fails(tree: Path) -> None:
    _plant(tree, CI, "permissions:\n  contents: read\n", "permissions: write-all\n")
    assert any("write-all" in item for item in _violations(tree))


def test_tag_pin_fails(tree: Path) -> None:
    _plant(tree, CI, CHECKOUT, "actions/checkout@v4")
    assert any(
        "not pinned to a full commit SHA: actions/checkout@v4" in item for item in _violations(tree)
    )


def test_pull_request_target_fails(tree: Path) -> None:
    _plant(tree, CI, "  pull_request:\n", "  pull_request_target:\n")
    assert any("pull_request_target" in item for item in _violations(tree))


def test_verify_without_full_history_fails(tree: Path) -> None:
    _plant(tree, PUBLISH, "          fetch-depth: 0\n", "          fetch-depth: 1\n")
    assert any("fetch-depth: 0" in item for item in _violations(tree))


def test_publish_top_level_permissions_must_be_empty(tree: Path) -> None:
    _plant(
        tree,
        PUBLISH,
        "permissions: {}\n\nconcurrency",
        "permissions:\n  contents: read\n\nconcurrency",
    )
    assert any("top-level permissions must be {}" in item for item in _violations(tree))


def test_empty_workflow_directory_fails(tmp_path: Path) -> None:
    (tmp_path / WORKFLOWS).mkdir(parents=True)
    assert main(["--root", str(tmp_path)]) == 1


def test_missing_publish_workflow_fails(tree: Path) -> None:
    (tree / PUBLISH).unlink()
    assert any("publish workflow is missing" in item for item in _violations(tree))


# --------------------------------------------------------------------------- #
# Digests come from the build job's outputs, never from an artifact
# --------------------------------------------------------------------------- #

DOWNLOAD = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
VERIFY_STEP = "Verify the files against the build job's outputs"


def _edit_publish(root: Path, edit: Callable[[dict[Any, Any]], None]) -> None:
    path = root / PUBLISH
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(workflow)
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")


def _steps_of(workflow: dict[Any, Any], job: str) -> list[dict[Any, Any]]:
    steps: list[dict[Any, Any]] = workflow["jobs"][job]["steps"]
    return steps


def _index_of(steps: list[dict[Any, Any]], name: str) -> int:
    matches = [i for i, step in enumerate(steps) if step.get("name") == name]
    assert len(matches) == 1, f"plant anchor {name!r} found {len(matches)} times"
    return matches[0]


def test_real_publish_verifies_every_distribution_download_against_build_outputs() -> None:
    workflow = yaml.safe_load((ROOT / PUBLISH).read_text(encoding="utf-8"))
    for job in ("install-matrix", "testpypi", "pypi", "github-release"):
        steps = _steps_of(workflow, job)
        verify = steps[_index_of(steps, VERIFY_STEP)]
        assert verify["env"]["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
        assert "build" in workflow["jobs"][job]["needs"]
    assert "sha256sums" not in (ROOT / PUBLISH).read_text(encoding="utf-8")


def test_downloading_a_digest_artifact_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "pypi")
        at = _index_of(steps, VERIFY_STEP) + 1
        steps.insert(at, {"uses": DOWNLOAD, "with": {"name": "sha256sums", "path": "sums"}})
        steps.insert(at + 1, dict(steps[at - 1]))  # still verified afterwards

    _edit_publish(tree, plant)
    assert any(
        "release.yml job pypi" in item and "downloads artifact 'sha256sums'" in item
        for item in _violations(tree)
    )


def test_downloading_every_artifact_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "install-matrix")
        at = _index_of(steps, VERIFY_STEP) + 1
        steps.insert(at, {"uses": DOWNLOAD, "with": {"path": "all"}})
        steps.insert(at + 1, dict(steps[at - 1]))

    _edit_publish(tree, plant)
    assert any("downloads artifact None" in item for item in _violations(tree))


def test_verify_step_moved_after_its_use_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "github-release")
        steps.append(steps.pop(_index_of(steps, VERIFY_STEP)))

    _edit_publish(tree, plant)
    assert any(
        "release.yml job github-release" in item and "the step after downloading" in item
        for item in _violations(tree)
    )


def test_upload_without_any_verification_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "pypi")
        del steps[_index_of(steps, VERIFY_STEP)]
        del steps[0]  # the download as well, so only the upload rule can see it

    _edit_publish(tree, plant)
    found = _violations(tree)
    assert any(
        "release.yml job pypi" in item and "uploads before verifying" in item for item in found
    )


def test_verify_step_reading_an_artifact_digest_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "testpypi")
        steps[_index_of(steps, VERIFY_STEP)]["env"]["WHEEL_SHA256"] = (
            "${{ steps.sums.outputs.sha }}"
        )

    _edit_publish(tree, plant)
    found = _violations(tree)
    assert any(
        "release.yml job testpypi" in item and "uploads before verifying" in item for item in found
    )


def test_verify_step_that_does_not_check_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "pypi")
        steps[_index_of(steps, VERIFY_STEP)]["run"] = "ls dist\n"

    _edit_publish(tree, plant)
    assert any(
        "release.yml job pypi" in item and "the step after downloading" in item
        for item in _violations(tree)
    )


def test_index_comparison_without_build_outputs_fails(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        steps = _steps_of(workflow, "testpypi-smoke")
        step = steps[_index_of(steps, "Prove TestPyPI serves the build job's bytes")]
        step["run"] = (
            "python scripts/compare_index_artifacts.py index --index-url https://test.pypi.org\n"
        )

    _edit_publish(tree, plant)
    assert any(
        "release.yml job testpypi-smoke" in item and "must --expect" in item
        for item in _violations(tree)
    )


def test_job_reading_build_outputs_must_need_build(tree: Path) -> None:
    def plant(workflow: dict[Any, Any]) -> None:
        workflow["jobs"]["pypi"]["needs"] = ["verify", "testpypi-smoke"]

    _edit_publish(tree, plant)
    assert any(
        "release.yml job pypi" in item and "does not need build" in item
        for item in _violations(tree)
    )
