"""Fail-closed controls for public repository policy and presentation files."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_has_read_only_required_jobs_and_supported_python_matrix() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {
        "integrity",
        "quality",
        "test",
        "build",
        "install-smoke",
        "workflow-policy",
        "release-tag-selftest",
    }
    assert workflow["jobs"]["build"]["uses"] == "./.github/workflows/build-distributions.yml"
    assert workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"] == [
        "3.11",
        "3.12",
        "3.13",
    ]


def test_ci_cannot_skip_source_checks_or_publish() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for forbidden in (
        "Detect Python project",
        "continue-on-error",
        "|| true",
        "pypi",
        "id-token: write",
        "twine upload",
    ):
        assert forbidden.casefold() not in workflow.casefold()


def test_ci_enforces_strict_installed_consumer_typing_examples() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m mypy --strict tests/typing" in workflow
    typing_example = ROOT / "tests" / "typing" / "authoring_contract.py"
    assert typing_example.is_file()
    assert "perceivo.orchestration.list_templates()" in typing_example.read_text(encoding="utf-8")


def test_notice_preserves_both_existing_attributions() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright 2026 ConceptBuilder AI" in notice
    assert "Copyright 2024-2026 ConceptBuilder AI" in notice
    assert (
        (ROOT / "LICENSE")
        .read_text(encoding="utf-8")
        .startswith("                                 Apache License")
    )


def test_release_file_documents_the_gated_pipeline() -> None:
    releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
    assert "No package upload happens any other" in releasing
    assert "Never delete the environment" in releasing
    assert "Repository visibility" in releasing
    assert "check_release_settings.py check" in releasing
    assert "ruleset-guard" in releasing
    assert "DRI" not in releasing


def test_contributor_claims_match_supported_versions_and_docs() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "https://perceivo.ai/docs" in contributing
    assert "Python 3.11, 3.12, or 3.13" in contributing
    assert "prepublication" not in contributing.lower()
    assert "not available yet" not in contributing
