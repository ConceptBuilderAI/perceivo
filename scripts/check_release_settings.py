#!/usr/bin/env python3
"""Read back the repository settings that gate a PyPI release. Never writes.

``check``            asserts the release gate (run before every tag push):

  * environment ``pypi``: ``can_admins_bypass`` is false; the required
    reviewers are exactly the ``--reviewer`` user, who is neither a
    ``--forbidden-reviewer`` nor the account running this check;
    ``prevent_self_review`` is true; and deployments are restricted to
    exactly one tag policy, ``v*``;
  * environment ``testpypi``: ``can_admins_bypass`` is false, and deployments
    are restricted to exactly one tag policy, ``v*``;
  * ruleset ``main-protection`` is active on the default branch with
    ``pull_request``, ``non_fast_forward``, ``deletion`` and
    ``required_status_checks`` covering every ``--required-checks`` name,
    and a ``pull_request`` rule that requires no approving review, no code
    owner review, and allows only the rebase merge method;
  * ruleset ``release-tags`` is active on ``refs/tags/v*`` and restricts tag
    creation, update and deletion.

``ruleset-snapshot`` prints a ruleset's ``rules``, ``conditions``,
``enforcement``, ``bypass_actors`` and ``target`` as JSON on stdout, before
anyone edits it. Redirect it to a file: this script writes no files.

``ruleset-guard``    re-reads the ruleset immediately before a write and
                     exits 1 unless it still equals the snapshot.

``ruleset-verify``   after the write, asserts the only change from the
                     snapshot is the ``--add`` required checks.

Snapshot files are plain ``*.json`` file names in the working directory; a
path, a ``..`` segment or an absolute name is refused.

Every GitHub read is ``gh api --method GET``. Settings are changed by a person,
or by the single writer named in the release runbook, never by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final

DEFAULT_REPOSITORY: Final = "ConceptBuilderAI/perceivo"
#: Comma-separated logins that may never be the required ``pypi`` reviewer, in
#: addition to any ``--forbidden-reviewer`` and the account running the check.
FORBIDDEN_REVIEWERS_ENV: Final = "RELEASE_FORBIDDEN_REVIEWERS"
TAG_PATTERN: Final = "v*"
MAIN_RULESET: Final = "main-protection"
TAG_RULESET: Final = "release-tags"
MAIN_RULE_TYPES: Final = ("pull_request", "non_fast_forward", "deletion", "required_status_checks")
TAG_RULE_TYPES: Final = ("creation", "update", "deletion")
# A release is one commit added on top of main, so rebase is the only merge
# method; parameter names follow the pull_request rule in GitHub's REST schema
# for repository rulesets (docs.github.com/en/rest/repos/rules).
MAIN_MERGE_METHODS: Final = ["rebase"]
MAIN_APPROVING_REVIEW_COUNT: Final = 0
DEFAULT_BRANCH_REFS: Final = frozenset({"~DEFAULT_BRANCH", "refs/heads/main"})
GUARDED_FIELDS: Final = ("target", "enforcement", "conditions", "rules", "bypass_actors")
SNAPSHOT_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.json")

Reader = Callable[[str], Any]


class SettingsError(Exception):
    """A setting cannot be read, or does not have the required value."""


def confine(path: Path, root: Path) -> Path:
    """Resolve ``path`` against ``root``; refuse anything that lands outside it.

    Symbolic links, ``..`` segments and absolute paths are all resolved first,
    so the check applies to the file that would actually be opened.
    """
    base = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(base, path))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise SettingsError(f"path {str(path)!r} is outside the allowed root {base!r}")
    return Path(candidate)


def gh_get(path: str) -> Any:
    """Read one GitHub API path with ``gh api --method GET``; ``None`` on 404."""
    command = [
        "gh",
        "api",
        "--method",
        "GET",
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        path,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        if "HTTP 404" in detail:
            return None
        raise SettingsError(f"gh api GET {path} failed: {detail[:300]}")
    return json.loads(completed.stdout) if completed.stdout.strip() else None


# --------------------------------------------------------------------------- #
# Environments
# --------------------------------------------------------------------------- #


def _environment(read: Reader, repository: str, name: str) -> dict[str, Any]:
    value = read(f"repos/{repository}/environments/{name}")
    if not isinstance(value, dict):
        raise SettingsError(f"environment {name} is absent")
    return value


def _rules_of_type(environment: dict[str, Any], rule_type: str) -> list[dict[str, Any]]:
    rules = environment.get("protection_rules") or []
    return [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == rule_type]


def _check_tag_policy(
    read: Reader, repository: str, name: str, environment: dict[str, Any]
) -> list[str]:
    problems: list[str] = []
    policy = environment.get("deployment_branch_policy")
    if not isinstance(policy, dict) or policy.get("custom_branch_policies") is not True:
        return [f"{name}: deployments are not restricted to selected tags"]
    if policy.get("protected_branches") is not False:
        problems.append(f"{name}: deployment policy admits protected branches")
    listing = read(f"repos/{repository}/environments/{name}/deployment-branch-policies")
    policies = listing.get("branch_policies") if isinstance(listing, dict) else None
    if not isinstance(policies, list):
        return [*problems, f"{name}: deployment branch policies cannot be read"]
    shapes = sorted((str(item.get("type", "branch")), str(item.get("name"))) for item in policies)
    if shapes != [("tag", TAG_PATTERN)]:
        problems.append(
            f"{name}: deployment policies must be exactly one tag rule {TAG_PATTERN!r}, found {shapes}"
        )
    return problems


def check_environment(
    read: Reader,
    repository: str,
    name: str,
    reviewer: str | None,
    forbidden: Iterable[str],
) -> list[str]:
    """Return every problem with one environment (empty when it passes)."""
    try:
        environment = _environment(read, repository, name)
    except SettingsError as exc:
        return [str(exc)]
    problems: list[str] = []
    if environment.get("can_admins_bypass") is not False:
        problems.append(
            f"{name}: can_admins_bypass is {environment.get('can_admins_bypass')!r}, must be false"
        )
    problems.extend(_check_tag_policy(read, repository, name, environment))
    if reviewer is None:
        return problems
    reviewer_rules = _rules_of_type(environment, "required_reviewers")
    if len(reviewer_rules) != 1:
        return [*problems, f"{name}: required reviewers are not configured"]
    rule = reviewer_rules[0]
    if rule.get("prevent_self_review") is not True:
        problems.append(
            f"{name}: prevent_self_review is {rule.get('prevent_self_review')!r}, must be true"
        )
    entries = rule.get("reviewers") or []
    identities = [
        (str(entry.get("type")), str((entry.get("reviewer") or {}).get("login", "")))
        for entry in entries
        if isinstance(entry, dict)
    ]
    if len(identities) != 1 or identities[0][0] != "User":
        problems.append(f"{name}: required reviewers must be exactly one user, found {identities}")
    elif identities[0][1].casefold() != reviewer.casefold():
        problems.append(f"{name}: required reviewer is {identities[0][1]!r}, expected {reviewer!r}")
    forbidden_folded = {login.casefold() for login in forbidden if login}
    for _, login in identities:
        if login.casefold() in forbidden_folded:
            problems.append(f"{name}: required reviewer {login!r} is a forbidden reviewer account")
    return problems


def default_forbidden_reviewers() -> list[str]:
    """Logins named by :data:`FORBIDDEN_REVIEWERS_ENV`; empty when it is unset."""
    raw = os.environ.get(FORBIDDEN_REVIEWERS_ENV, "")
    return [login.strip() for login in raw.split(",") if login.strip()]


# --------------------------------------------------------------------------- #
# Rulesets
# --------------------------------------------------------------------------- #


def find_ruleset(read: Reader, repository: str, name: str) -> dict[str, Any]:
    listing = read(f"repos/{repository}/rulesets?per_page=100&includes_parents=false")
    if not isinstance(listing, list):
        raise SettingsError("rulesets cannot be listed")
    matches = [item for item in listing if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise SettingsError(f"expected exactly one ruleset named {name!r}, found {len(matches)}")
    return read_ruleset(read, repository, int(matches[0]["id"]))


def read_ruleset(read: Reader, repository: str, ruleset_id: int) -> dict[str, Any]:
    value = read(f"repos/{repository}/rulesets/{ruleset_id}")
    if not isinstance(value, dict):
        raise SettingsError(f"ruleset {ruleset_id} is absent")
    return value


def _rule_types(ruleset: dict[str, Any]) -> set[str]:
    return {str(rule.get("type")) for rule in ruleset.get("rules") or [] if isinstance(rule, dict)}


def required_contexts(ruleset: dict[str, Any]) -> list[dict[str, Any]]:
    for rule in ruleset.get("rules") or []:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            checks = (rule.get("parameters") or {}).get("required_status_checks") or []
            return [check for check in checks if isinstance(check, dict)]
    return []


def _rule_parameters(ruleset: dict[str, Any], rule_type: str) -> dict[str, Any] | None:
    for rule in ruleset.get("rules") or []:
        if isinstance(rule, dict) and rule.get("type") == rule_type:
            parameters = rule.get("parameters")
            return parameters if isinstance(parameters, dict) else {}
    return None


def _check_pull_request_rule(parameters: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    count = parameters.get("required_approving_review_count")
    # bool is an int subclass, and False == 0 would otherwise pass.
    if type(count) is not int or count != MAIN_APPROVING_REVIEW_COUNT:
        problems.append(
            f"{MAIN_RULESET}: required_approving_review_count is {count!r}, "
            f"must be {MAIN_APPROVING_REVIEW_COUNT}"
        )
    # A code owner review is an approval, and an author cannot approve their own
    # pull request, so requiring one would block a release with no approvals.
    code_owner = parameters.get("require_code_owner_review")
    if code_owner is not False:
        problems.append(
            f"{MAIN_RULESET}: require_code_owner_review is {code_owner!r}, must be false"
        )
    methods = parameters.get("allowed_merge_methods")
    if methods != MAIN_MERGE_METHODS:
        problems.append(
            f"{MAIN_RULESET}: allowed_merge_methods is {methods!r}, must be {MAIN_MERGE_METHODS!r}"
        )
    return problems


def _includes(ruleset: dict[str, Any]) -> set[str]:
    conditions = ruleset.get("conditions") or {}
    ref_name = conditions.get("ref_name") or {}
    return {str(item) for item in ref_name.get("include") or []}


def _check_main_ruleset(main: dict[str, Any], required_checks: Iterable[str]) -> list[str]:
    problems: list[str] = []
    if main.get("enforcement") != "active":
        problems.append(
            f"{MAIN_RULESET}: enforcement is {main.get('enforcement')!r}, must be active"
        )
    if main.get("target") != "branch" or not _includes(main) & DEFAULT_BRANCH_REFS:
        problems.append(f"{MAIN_RULESET}: does not target the default branch")
    missing_rules = [rule for rule in MAIN_RULE_TYPES if rule not in _rule_types(main)]
    if missing_rules:
        problems.append(f"{MAIN_RULESET}: missing rules {missing_rules}")
    pull_request = _rule_parameters(main, "pull_request")
    if pull_request is not None:
        problems += _check_pull_request_rule(pull_request)
    present = {str(check.get("context")) for check in required_contexts(main)}
    missing_checks = sorted(set(required_checks) - present)
    if missing_checks:
        problems.append(f"{MAIN_RULESET}: required checks missing {missing_checks}")
    return problems


def _check_tag_ruleset(tags: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if tags.get("enforcement") != "active":
        problems.append(
            f"{TAG_RULESET}: enforcement is {tags.get('enforcement')!r}, must be active"
        )
    if tags.get("target") != "tag" or f"refs/tags/{TAG_PATTERN}" not in _includes(tags):
        problems.append(f"{TAG_RULESET}: does not target refs/tags/{TAG_PATTERN}")
    missing_rules = [rule for rule in TAG_RULE_TYPES if rule not in _rule_types(tags)]
    if missing_rules:
        problems.append(f"{TAG_RULESET}: missing rules {missing_rules}")
    return problems


def check_rulesets(read: Reader, repository: str, required_checks: Iterable[str]) -> list[str]:
    problems: list[str] = []
    try:
        problems += _check_main_ruleset(
            find_ruleset(read, repository, MAIN_RULESET), required_checks
        )
    except SettingsError as exc:
        problems.append(str(exc))
    try:
        problems += _check_tag_ruleset(find_ruleset(read, repository, TAG_RULESET))
    except SettingsError as exc:
        problems.append(str(exc))
    return problems


def check_release_settings(
    read: Reader,
    repository: str,
    reviewer: str,
    required_checks: Iterable[str],
    forbidden: Iterable[str],
) -> list[str]:
    """Return every problem with the release gate (empty when it passes)."""
    forbidden_all = {*forbidden}
    user = read("user")
    if isinstance(user, dict) and user.get("login"):
        forbidden_all.add(str(user["login"]))
    problems: list[str] = []
    if reviewer.casefold() in {login.casefold() for login in forbidden_all}:
        problems.append(f"--reviewer {reviewer!r} is a forbidden reviewer account")
    problems += check_environment(read, repository, "pypi", reviewer, forbidden_all)
    problems += check_environment(read, repository, "testpypi", None, forbidden_all)
    problems += check_rulesets(read, repository, required_checks)
    return problems


# --------------------------------------------------------------------------- #
# Guarded ruleset edits (snapshot -> guard -> write by a person -> verify)
# --------------------------------------------------------------------------- #


def _guarded_view(ruleset: dict[str, Any]) -> dict[str, Any]:
    return {field: ruleset.get(field) for field in GUARDED_FIELDS}


def snapshot(read: Reader, repository: str, name: str) -> dict[str, Any]:
    ruleset = find_ruleset(read, repository, name)
    return {
        "repository": repository,
        "ruleset_id": int(ruleset["id"]),
        "name": name,
        "guarded": _guarded_view(ruleset),
    }


def snapshot_file(name: str) -> Path:
    """A snapshot file: a plain ``*.json`` file name in the working directory."""
    base_name = os.path.basename(name)
    if base_name != name or not SNAPSHOT_NAME.fullmatch(base_name):
        raise SettingsError(
            f"snapshot {name!r} must be a plain .json file name in the working directory"
        )
    return confine(Path(base_name), Path.cwd())


def _load_snapshot(path: Path, root: Path) -> dict[str, Any]:
    # utf-8-sig: Windows PowerShell 5.1 writes a BOM on `> snapshot.json`.
    value = json.loads(confine(path, root).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not {"repository", "ruleset_id", "guarded"} <= set(value):
        raise SettingsError(f"{path} is not a ruleset snapshot")
    return value


def _differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    problems = []
    for field in GUARDED_FIELDS:
        if before.get(field) != after.get(field):
            problems.append(f"{field} changed")
    before_checks = {str(check.get("context")) for check in required_contexts(before)}
    after_checks = {str(check.get("context")) for check in required_contexts(after)}
    if added := sorted(after_checks - before_checks):
        problems.append(f"required checks added since snapshot: {added}")
    if removed := sorted(before_checks - after_checks):
        problems.append(f"required checks removed since snapshot: {removed}")
    return problems


def guard(read: Reader, saved: dict[str, Any]) -> list[str]:
    """Empty when the live ruleset still equals the snapshot."""
    live = _guarded_view(read_ruleset(read, str(saved["repository"]), int(saved["ruleset_id"])))
    return _differences(saved["guarded"], live)


def _without_required_checks(view: dict[str, Any]) -> dict[str, Any]:
    stripped = dict(view)
    rules = []
    for rule in view.get("rules") or []:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            parameters = dict(rule.get("parameters") or {})
            parameters.pop("required_status_checks", None)
            rules.append({**rule, "parameters": parameters})
        else:
            rules.append(rule)
    stripped["rules"] = sorted(rules, key=lambda item: json.dumps(item, sort_keys=True))
    return stripped


def verify_added(read: Reader, saved: dict[str, Any], added: Iterable[str]) -> list[str]:
    """Empty when the only change since the snapshot is exactly ``added``."""
    before = saved["guarded"]
    after = _guarded_view(read_ruleset(read, str(saved["repository"]), int(saved["ruleset_id"])))
    problems: list[str] = []
    before_rules = {
        rule.get("type") for rule in before.get("rules") or [] if isinstance(rule, dict)
    }
    comparable_before = _without_required_checks(before)
    comparable_after = _without_required_checks(after)
    if "required_status_checks" not in before_rules:
        comparable_after["rules"] = [
            rule
            for rule in comparable_after["rules"]
            if not (isinstance(rule, dict) and rule.get("type") == "required_status_checks")
        ]
    for field in GUARDED_FIELDS:
        if comparable_before.get(field) != comparable_after.get(field):
            problems.append(f"{field} changed beyond the required checks")
    before_checks = {str(check.get("context")): check for check in required_contexts(before)}
    after_checks = {str(check.get("context")): check for check in required_contexts(after)}
    expected = set(before_checks) | set(added)
    if set(after_checks) != expected:
        problems.append(f"required checks are {sorted(after_checks)}, expected {sorted(expected)}")
    for context, check in before_checks.items():
        if context in after_checks and after_checks[context] != check:
            problems.append(f"required check {context!r} changed its source")
    return problems


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--reviewer", required=True, help="the only allowed pypi reviewer login")
    check.add_argument("--required-checks", nargs="*", default=[])
    check.add_argument(
        "--forbidden-reviewer", action="append", default=default_forbidden_reviewers()
    )
    snap = subparsers.add_parser("ruleset-snapshot")
    snap.add_argument("name")
    guard_parser = subparsers.add_parser("ruleset-guard")
    guard_parser.add_argument("--pre", required=True)
    verify = subparsers.add_parser("ruleset-verify")
    verify.add_argument("--pre", required=True)
    verify.add_argument("--add", nargs="+", required=True)
    return parser


def main(argv: list[str] | None = None, read: Reader = gh_get) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "ruleset-snapshot":
            saved = snapshot(read, args.repo, args.name)
            print(json.dumps(saved, indent=2, sort_keys=True))
            print(
                f"snapshot taken: ruleset {saved['name']} ({saved['ruleset_id']})",
                file=sys.stderr,
            )
            return 0
        if args.command == "check":
            problems = check_release_settings(
                read, args.repo, args.reviewer, args.required_checks, args.forbidden_reviewer
            )
        elif args.command == "ruleset-guard":
            problems = guard(read, _load_snapshot(snapshot_file(args.pre), Path.cwd()))
        else:
            problems = verify_added(
                read, _load_snapshot(snapshot_file(args.pre), Path.cwd()), args.add
            )
    except (SettingsError, OSError, ValueError, KeyError) as exc:
        print(f"release settings rejected: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"problem: {problem}", file=sys.stderr)
    if problems:
        print(f"release settings rejected: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"release settings passed: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
