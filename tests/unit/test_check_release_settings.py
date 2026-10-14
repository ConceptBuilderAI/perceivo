"""Read-back of the release gate, and the guarded ruleset-edit procedure."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import check_release_settings as settings
from scripts.check_release_settings import main


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CLI paths are confined to the working directory.
    monkeypatch.chdir(tmp_path)


REPO = "ConceptBuilderAI/perceivo"
REVIEWER = "release-human"
CHECKS = ["Repository integrity", "Workflow policy"]


def _environment(name: str, reviewers: bool) -> dict[str, Any]:
    rules: list[dict[str, Any]] = [{"type": "branch_policy"}]
    if reviewers:
        rules.insert(
            0,
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "User", "reviewer": {"login": REVIEWER}}],
            },
        )
    return {
        "name": name,
        "can_admins_bypass": False,
        "protection_rules": rules,
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
    }


def _main_ruleset() -> dict[str, Any]:
    return {
        "id": 11,
        "name": "main-protection",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "require_code_owner_review": False,
                    "allowed_merge_methods": ["rebase"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [
                        {"context": name, "integration_id": 15368} for name in CHECKS
                    ],
                },
            },
        ],
    }


def _tag_ruleset() -> dict[str, Any]:
    return {
        "id": 12,
        "name": "release-tags",
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5}],
        "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
    }


def _state() -> dict[str, Any]:
    tag_policy = {"total_count": 1, "branch_policies": [{"name": "v*", "type": "tag"}]}
    return {
        "user": {"login": "agent-session"},
        f"repos/{REPO}/environments/pypi": _environment("pypi", reviewers=True),
        f"repos/{REPO}/environments/testpypi": _environment("testpypi", reviewers=False),
        f"repos/{REPO}/environments/pypi/deployment-branch-policies": copy.deepcopy(tag_policy),
        f"repos/{REPO}/environments/testpypi/deployment-branch-policies": copy.deepcopy(tag_policy),
        f"repos/{REPO}/rulesets?per_page=100&includes_parents=false": [
            {"id": 11, "name": "main-protection"},
            {"id": 12, "name": "release-tags"},
        ],
        f"repos/{REPO}/rulesets/11": _main_ruleset(),
        f"repos/{REPO}/rulesets/12": _tag_ruleset(),
    }


class Reader:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def __call__(self, path: str) -> Any:
        return copy.deepcopy(self.state.get(path))


def _check(state: dict[str, Any], reviewer: str = REVIEWER) -> int:
    return main(
        ["--repo", REPO, "check", "--reviewer", reviewer, "--required-checks", *CHECKS],
        read=Reader(state),
    )


def _pypi(state: dict[str, Any]) -> dict[str, Any]:
    environment: dict[str, Any] = state[f"repos/{REPO}/environments/pypi"]
    return environment


def test_passing_gate() -> None:
    assert _check(_state()) == 0


def test_pypi_admin_bypass_fails(capsys: pytest.CaptureFixture[str]) -> None:
    state = _state()
    _pypi(state)["can_admins_bypass"] = True
    assert _check(state) == 1
    assert "pypi: can_admins_bypass is True, must be false" in capsys.readouterr().err


def test_testpypi_admin_bypass_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/environments/testpypi"]["can_admins_bypass"] = True
    assert _check(state) == 1


def test_admin_bypass_absent_fails() -> None:
    state = _state()
    del _pypi(state)["can_admins_bypass"]
    assert _check(state) == 1


def test_reviewer_is_configured_forbidden_login_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(settings.FORBIDDEN_REVIEWERS_ENV, "release-operator, other-operator")
    state = _state()
    _pypi(state)["protection_rules"][0]["reviewers"][0]["reviewer"]["login"] = "release-operator"
    assert _check(state, reviewer="release-operator") == 1


def test_forbidden_reviewers_default_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(settings.FORBIDDEN_REVIEWERS_ENV, raising=False)
    assert settings.default_forbidden_reviewers() == []
    monkeypatch.setenv(settings.FORBIDDEN_REVIEWERS_ENV, " a ,, b ")
    assert settings.default_forbidden_reviewers() == ["a", "b"]


def test_reviewer_is_session_login_fails() -> None:
    state = _state()
    state["user"] = {"login": REVIEWER}
    assert _check(state) == 1


def test_different_reviewer_fails() -> None:
    state = _state()
    _pypi(state)["protection_rules"][0]["reviewers"][0]["reviewer"]["login"] = "someone-else"
    assert _check(state) == 1


def test_extra_reviewer_fails() -> None:
    state = _state()
    _pypi(state)["protection_rules"][0]["reviewers"].append(
        {"type": "User", "reviewer": {"login": "release-operator"}}
    )
    assert _check(state) == 1


def test_team_reviewer_fails() -> None:
    state = _state()
    _pypi(state)["protection_rules"][0]["reviewers"][0]["type"] = "Team"
    assert _check(state) == 1


def test_no_reviewers_fails() -> None:
    state = _state()
    _pypi(state)["protection_rules"].pop(0)
    assert _check(state) == 1


def test_self_review_allowed_fails() -> None:
    state = _state()
    _pypi(state)["protection_rules"][0]["prevent_self_review"] = False
    assert _check(state) == 1


def test_no_tag_policy_fails() -> None:
    state = _state()
    _pypi(state)["deployment_branch_policy"] = None
    assert _check(state) == 1


def test_branch_policy_instead_of_tag_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/environments/pypi/deployment-branch-policies"]["branch_policies"] = [
        {"name": "main", "type": "branch"}
    ]
    assert _check(state) == 1


def test_extra_tag_policy_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/environments/testpypi/deployment-branch-policies"][
        "branch_policies"
    ].append({"name": "*", "type": "tag"})
    assert _check(state) == 1


def test_pypi_absent_fails(capsys: pytest.CaptureFixture[str]) -> None:
    state = _state()
    del state[f"repos/{REPO}/environments/pypi"]
    assert _check(state) == 1
    assert "environment pypi is absent" in capsys.readouterr().err


def test_required_check_missing_fails(capsys: pytest.CaptureFixture[str]) -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/11"]["rules"][3]["parameters"]["required_status_checks"].pop()
    assert _check(state) == 1
    assert "required checks missing ['Workflow policy']" in capsys.readouterr().err


def test_main_ruleset_disabled_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/11"]["enforcement"] = "evaluate"
    assert _check(state) == 1


def test_main_ruleset_missing_rule_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/11"]["rules"].pop(1)
    assert _check(state) == 1


@pytest.mark.parametrize("count", [1, 2, None, False, "0"])
def test_approving_review_count_other_than_zero_fails(
    count: object, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/11"]["rules"][2]["parameters"][
        "required_approving_review_count"
    ] = count
    assert _check(state) == 1
    assert f"required_approving_review_count is {count!r}, must be 0" in capsys.readouterr().err


@pytest.mark.parametrize("required", [True, None, "false"])
def test_code_owner_review_other_than_false_fails(
    required: object, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/11"]["rules"][2]["parameters"]["require_code_owner_review"] = (
        required
    )
    assert _check(state) == 1
    assert f"require_code_owner_review is {required!r}, must be false" in capsys.readouterr().err


@pytest.mark.parametrize(
    "methods", [["rebase", "squash"], ["merge"], ["squash"], [], None, "rebase"]
)
def test_merge_methods_other_than_rebase_only_fail(
    methods: object, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/11"]["rules"][2]["parameters"]["allowed_merge_methods"] = methods
    assert _check(state) == 1
    assert f"allowed_merge_methods is {methods!r}, must be ['rebase']" in capsys.readouterr().err


def test_tag_ruleset_absent_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets?per_page=100&includes_parents=false"].pop()
    assert _check(state) == 1


def test_tag_ruleset_wrong_pattern_fails() -> None:
    state = _state()
    state[f"repos/{REPO}/rulesets/12"]["conditions"]["ref_name"]["include"] = ["refs/tags/*"]
    assert _check(state) == 1


# --------------------------------------------------------------------------- #
# Guarded edit of main-protection (O-5 API path)
# --------------------------------------------------------------------------- #


def _snapshot(tmp_path: Path, state: dict[str, Any], name: str = "snapshot.json") -> Path:
    """What an operator gets from ``ruleset-snapshot main-protection > name``."""
    path = tmp_path / name
    saved = settings.snapshot(Reader(state), REPO, "main-protection")
    path.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_snapshot_prints_pure_json_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = sorted(tmp_path.iterdir())
    assert main(["--repo", REPO, "ruleset-snapshot", "main-protection"], read=Reader(_state())) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == settings.snapshot(Reader(_state()), REPO, "main-protection")
    assert "snapshot taken" in captured.err
    assert sorted(tmp_path.iterdir()) == before


def test_guard_passes_when_unchanged(tmp_path: Path) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    assert (
        main(
            ["--repo", REPO, "ruleset-guard", "--pre", path.name],
            read=Reader(state),
        )
        == 0
    )


def test_guard_fails_on_check_added_after_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    state[f"repos/{REPO}/rulesets/11"]["rules"][3]["parameters"]["required_status_checks"].append(
        {"context": "planted-concurrent-check", "integration_id": 15368}
    )
    assert (
        main(
            ["--repo", REPO, "ruleset-guard", "--pre", path.name],
            read=Reader(state),
        )
        == 1
    )
    assert (
        "required checks added since snapshot: ['planted-concurrent-check']"
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enforcement", "disabled"),
        ("conditions", {"ref_name": {"include": ["refs/heads/other"], "exclude": []}}),
        ("bypass_actors", [{"actor_type": "RepositoryRole", "actor_id": 5}]),
        ("rules", [{"type": "deletion"}]),
    ],
)
def test_guard_fails_on_any_guarded_field(tmp_path: Path, field: str, value: object) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    state[f"repos/{REPO}/rulesets/11"][field] = value
    assert (
        main(
            ["--repo", REPO, "ruleset-guard", "--pre", path.name],
            read=Reader(state),
        )
        == 1
    )


def _add_checks(state: dict[str, Any], *names: str) -> None:
    state[f"repos/{REPO}/rulesets/11"]["rules"][3]["parameters"]["required_status_checks"].extend(
        {"context": name, "integration_id": 15368} for name in names
    )


def test_verify_passes_when_only_the_added_checks_changed(tmp_path: Path) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    _add_checks(state, "Build / Build and reproduce distributions")
    args = [
        "--repo",
        REPO,
        "ruleset-verify",
        "--pre",
        path.name,
        "--add",
        "Build / Build and reproduce distributions",
    ]
    assert main(args, read=Reader(state)) == 0


def test_verify_fails_on_foreign_extra_check(tmp_path: Path) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    _add_checks(state, "new", "foreign")
    assert (
        main(
            [
                "--repo",
                REPO,
                "ruleset-verify",
                "--pre",
                path.name,
                "--add",
                "new",
            ],
            read=Reader(state),
        )
        == 1
    )


def test_verify_fails_when_added_check_absent(tmp_path: Path) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    assert (
        main(
            [
                "--repo",
                REPO,
                "ruleset-verify",
                "--pre",
                path.name,
                "--add",
                "new",
            ],
            read=Reader(state),
        )
        == 1
    )


def test_verify_fails_when_existing_check_removed_or_rebound(tmp_path: Path) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    checks = state[f"repos/{REPO}/rulesets/11"]["rules"][3]["parameters"]["required_status_checks"]
    checks[0]["integration_id"] = 999
    _add_checks(state, "new")
    assert (
        main(
            [
                "--repo",
                REPO,
                "ruleset-verify",
                "--pre",
                path.name,
                "--add",
                "new",
            ],
            read=Reader(state),
        )
        == 1
    )


def test_verify_fails_when_other_rule_changed(tmp_path: Path) -> None:
    state = _state()
    path = _snapshot(tmp_path, state)
    _add_checks(state, "new")
    state[f"repos/{REPO}/rulesets/11"]["rules"].pop(0)
    assert (
        main(
            [
                "--repo",
                REPO,
                "ruleset-verify",
                "--pre",
                path.name,
                "--add",
                "new",
            ],
            read=Reader(state),
        )
        == 1
    )


# --------------------------------------------------------------------------- #
# The script never writes
# --------------------------------------------------------------------------- #


def test_gh_reads_are_get_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "gh: Not Found (HTTP 404)")

    monkeypatch.setattr(settings.subprocess, "run", fake_run)
    assert main(["--repo", REPO, "check", "--reviewer", REVIEWER]) == 1
    assert calls, "no GitHub read was attempted"
    for command in calls:
        assert command[:4] == ["gh", "api", "--method", "GET"]
        assert not {"-f", "-F", "--field", "--raw-field", "--input", "-X"} & set(command)


def test_source_names_no_write_method() -> None:
    source = Path(settings.__file__).read_text(encoding="utf-8")
    assert not re.search(r'"(POST|PUT|PATCH|DELETE)"', source)
    assert source.count('"--method"') == 1


def test_snapshot_file_is_json(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, _state())
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["ruleset_id"] == 11
    assert set(saved["guarded"]) == set(settings.GUARDED_FIELDS)


def test_environment_check_refuses_forbidden_reviewer_on_its_own() -> None:
    state = _state()
    login = "release-operator"
    _pypi(state)["protection_rules"][0]["reviewers"][0]["reviewer"]["login"] = login
    problems = settings.check_environment(Reader(state), REPO, "pypi", login, [login])
    assert any("is a forbidden reviewer account" in problem for problem in problems)


def test_gate_refuses_forbidden_reviewer_argument_on_its_own() -> None:
    problems = settings.check_release_settings(Reader(_state()), REPO, "agent-session", CHECKS, [])
    assert any(problem.startswith("--reviewer 'agent-session'") for problem in problems)


# --------------------------------------------------------------------------- #
# Snapshot paths are confined to the working directory
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["ruleset-guard", "ruleset-verify"])
@pytest.mark.parametrize(
    "escape", ["../outside.json", "OUTSIDE_ABSOLUTE", "sub/../../outside.json", "outside"]
)
def test_snapshot_path_outside_root_is_refused(
    tmp_path: Path,
    command: str,
    escape: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    # A real, valid snapshot sits outside the root: only confinement refuses it.
    outside = _snapshot(tmp_path, _state(), "outside.json")
    monkeypatch.chdir(root)
    target = str(outside) if escape == "OUTSIDE_ABSOLUTE" else escape
    extra = ["--add", "x"] if command == "ruleset-verify" else []
    argv = ["--repo", REPO, command, "--pre", target, *extra]
    assert main(argv, read=Reader(_state())) == 1
    assert "must be a plain .json file name" in capsys.readouterr().err


def test_confine_accepts_a_path_inside_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    assert (
        settings.confine(Path("sub/../sub/snap.json"), tmp_path)
        == (tmp_path / "sub" / "snap.json").resolve()
    )


@pytest.mark.parametrize("name", ["snap.json", "O5-main.snapshot.json"])
def test_snapshot_file_accepts_plain_names(tmp_path: Path, name: str) -> None:
    assert settings.snapshot_file(name) == (tmp_path / name).resolve()


@pytest.mark.parametrize(
    "name",
    ["../x.json", "sub/x.json", "sub\\x.json", "/tmp/x.json", "-x.json", "x.txt", ".json", ""],
)
def test_snapshot_file_refuses_paths(name: str) -> None:
    with pytest.raises(settings.SettingsError, match=r"plain \.json file name"):
        settings.snapshot_file(name)


@pytest.mark.parametrize("escape", ["../outside.json", "OUTSIDE_ABSOLUTE"])
def test_confine_refuses_escape(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = Path(tmp_path / "outside.json") if escape == "OUTSIDE_ABSOLUTE" else Path(escape)
    with pytest.raises(settings.SettingsError, match="outside the allowed root"):
        settings.confine(target, root)


def test_load_snapshot_refuses_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(settings.snapshot(Reader(_state()), REPO, "main-protection")), encoding="utf-8"
    )
    assert settings._load_snapshot(Path("outside.json"), tmp_path)["ruleset_id"] == 11  # control
    escape = Path("../outside.json")
    with pytest.raises(settings.SettingsError, match="outside the allowed root"):
        settings._load_snapshot(escape, root)


def test_confine_refuses_sibling_prefix_directory(tmp_path: Path) -> None:
    # "<root>-evil" starts with the root's own name; only the separator
    # boundary keeps it out.
    root = tmp_path / "base"
    root.mkdir()
    (tmp_path / "base-evil").mkdir()
    sibling = Path("../base-evil/snap.json")
    with pytest.raises(settings.SettingsError, match="outside the allowed root"):
        settings.confine(sibling, root)


def test_snapshot_with_utf8_bom_is_read(tmp_path: Path) -> None:
    # Windows PowerShell 5.1 writes a BOM on `> snapshot.json`.
    state = _state()
    saved = settings.snapshot(Reader(state), REPO, "main-protection")
    (tmp_path / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(saved).encode("utf-8"))
    argv = ["--repo", REPO, "ruleset-guard", "--pre", "bom.json"]
    assert main(argv, read=Reader(state)) == 0
