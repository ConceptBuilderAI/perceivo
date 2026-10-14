"""Vendor the platform template library into ``perceivo/templates/_library/``.

Reads every template bundle from a platform data service checkout at
one commit, rewrites them to the frozen SDK surface, and writes each rewritten
bundle plus ``SOURCE.json``. The platform commit and the platform paths read
from it are private, so they are recorded beside this script, in
``scripts/template_library_upstream.json``, which the public export does not carry.

    python scripts/vendor_template_library.py <platform checkout> [--commit SHA]
    python scripts/vendor_template_library.py <platform checkout> --check

``--check`` re-derives every file from the platform commit recorded in
``template_library_upstream.json`` and byte-compares it with what is committed; it exits non-zero
on any difference. SDK CI cannot read the platform, so the check is run by hand.

**The rewrite is the whole of the change.** A vendored bundle is the library
asset's own content with exactly these edits, each a one-way rewrite to the
frozen surface (the same table the seed rewrite applies to the platform, so that after it
the rewrite is the identity):

1. ``perceivo.<Name>`` -> ``perceivo.primitives.<Name>`` for the primitive
   classes, their ``*Input``/``*Output`` types, ``Artifact`` and
   ``ArtifactType``.
2. ``perceivo.LayerInput``/``LayerOutput`` -> ``perceivo.primitives.FsmInput``/
   ``FsmOutput``.
3. ``parallel-executor`` only: drop the ``fail_fast=`` keyword to
   ``flow.invoke_parallel`` and the dead ``fail_fast = ...`` line before it.
   ``failure_strategy`` is already passed.
5. ``error-handler`` only: the Primary's ``fail_mode: never`` return becomes the
   constant-key literal its manifest declares, and its docstring, manifest,
   parameter descriptions and bundle description stop claiming the slot
   forwards what it received (``RULE5_PROSE``).
6. Parenthesised private ticket references ``(TICK-n)`` are dropped; the SDK
   ships no private ticket identifiers. The pattern is not limited to comments,
   so every re-vendor prints each rule-6 hit: re-check that each one is still a
   comment before committing.
7. The ``log=`` keyword is dropped from every ``*Output(`` call in a script: the
   ``log`` field does not exist on any Output type, so a vendored script that
   passes it raises ``TypeError`` on its first run. Found by AST, not by text:
   the keyword must occupy whole lines ending in ``,`` and those lines are
   deleted. Any other shape refuses the vendor rather than guessing.
8. A platform service named by its repository (``perceivo-<name>-service`` and
   the other repository shapes) becomes a description, ``the platform <name>
   service``: the public SDK names no internal repository. The rule is a
   shape, not a list, and a vendored file in which such a name survives
   refuses the vendor.
9. ``error-handler`` only: the clause of its script that cites the platform
   engine's source file is dropped, keeping the engine message it introduces:
   the public SDK cites no platform source. The clause must occur exactly once,
   so a bundle in which it moved or multiplied refuses the vendor.

Decorators (``@perceivo.<d>``) are left as they are: they are the root
re-exports. The bundles' JSON is re-serialised exactly as the platform writes it
(``indent=2``, ``ensure_ascii=False``, trailing newline), so an identity rewrite
reproduces the source bytes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SDK_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = SDK_ROOT / "perceivo" / "templates" / "_library"
UPSTREAM_JSON = SDK_ROOT / "scripts" / "template_library_upstream.json"


def upstream() -> dict[str, str]:
    """The platform commit, and the bundle, catalog and walker-id paths read from it."""
    loaded: dict[str, str] = json.loads(UPSTREAM_JSON.read_text(encoding="utf-8"))
    return loaded


#: Every rule this script applies, by number.
RULES: tuple[int, ...] = (1, 2, 3, 5, 6, 7, 8, 9)

_PRIMITIVE = (
    r"(?:Start|End|Agent|Tool|FSM|Fsm|Policy|Adapter|Review)(?:Input|Output)?"
    r"|ArtifactType|Artifact"
)

RULE5_RETURN_OLD = """response_data={
                **request_data,
                "primary_ran": True,
                "primary_failed": False,
            }"""
RULE5_RETURN_NEW = """response_data={
                "primary_ran": True,
                "primary_failed": False,
            }"""
RULE5_DOC_OLD = """With fail_mode set to "never" it succeeds and forwards
    whatever it received, which is what the template looks like once the real
    work is wired in."""
RULE5_DOC_NEW = """With fail_mode set to "never" it succeeds and returns
    primary_ran and primary_failed, which is what the template looks like once
    the real work is wired in."""
RULE5_MANIFEST_OLD = """or set
  fail_mode to never to make it a pass-through."""
RULE5_MANIFEST_NEW = """or set
  fail_mode to never to make it succeed, returning primary_ran and primary_failed."""
#: Rule 5, the Primary's prose: every string that says ``fail_mode: never``
#: passes data through is rewritten to say what the slot does -- it succeeds and returns
#: ``primary_ran`` and ``primary_failed``. Each phrase occurs once in the bundle.
RULE5_PROSE: tuple[tuple[str, str], ...] = (
    (RULE5_MANIFEST_OLD, RULE5_MANIFEST_NEW),
    (
        "never, succeed and pass data through.",
        "never, succeed and return primary_ran and primary_failed.",
    ),
    (
        "never: succeed and pass data through.",
        "never: succeed and return primary_ran and primary_failed.",
    ),
    (
        "and it passes its input straight through instead.",
        "and it succeeds instead, returning primary_ran and primary_failed.",
    ),
    (
        "- Fail Mode - raise on every call, or succeed and pass data through.",
        "- Fail Mode - raise on every call, or succeed and return primary_ran and primary_failed.",
    ),
    (
        "Out: the same data when Fail Mode is never; nothing when it raises.",
        "Out: primary_ran and primary_failed when Fail Mode is never; nothing when it raises.",
    ),
)
_RULE6 = re.compile(r" \(TICK-\d+\)")

#: Rule 8: a platform repository name, by shape.
_RULE8 = re.compile(
    r"\bperceivo-((?:[a-z]+-)*?[a-z]+)-(service|manager|microservice|broker|gateway)\b"
)


#: Rule 9: ``the platform <service>, app/<path>.py:`` and the comment marker after it.
_RULE9 = re.compile(r"the platform [a-z ]+, app/[\w/]+\.py:\n# ")


def rule9_hits(value: Any) -> int:
    """How many times rule 9's clause occurs across every string in a parsed bundle."""
    if isinstance(value, str):
        return len(_RULE9.findall(value))
    if isinstance(value, list):
        return sum(rule9_hits(item) for item in value)
    if isinstance(value, dict):
        return sum(rule9_hits(item) for item in value.values())
    return 0


def describe_repositories(text: str) -> str:
    """Rule 8: ``perceivo-<name>-<kind>`` -> ``the platform <name> <kind>``."""

    def describe(match: re.Match[str]) -> str:
        name, kind = match.group(1).replace("-", " "), match.group(2)
        return f"the platform {name} {'service' if kind == 'microservice' else kind}"

    return _RULE8.sub(describe, text)


def refuse_repository_names(path: str, vendored: bytes) -> None:
    """Rule 8's backstop: a vendored file must carry no repository-shaped name."""
    if _RULE8.search(vendored.decode("utf-8")):
        raise SystemExit(f"{path}: a platform repository name survives the rewrite")


#: Rule 7: the callee name of an Output constructor, bare or qualified.
OUTPUT_CALLEE = re.compile(r"^(?:Agent|Tool|Fsm|FSM|Policy|Review|Adapter)Output$")
_LOG_KEYWORD = re.compile(r"(?<![\w.])log\s*=")
_LINE_BREAK = re.compile(r"(\r\n|\r|\n)")


def split_lines(text: str) -> list[str]:
    r"""``text`` as lines, each keeping its own ``\r\n``, ``\r`` or ``\n`` ending.

    Unlike :meth:`str.splitlines` it breaks on those three endings only, so a
    ``\f`` or `` `` inside a string literal stays inside its line. One
    linear ``re.split``: the pieces alternate text, ending, text, ... and end
    with the trailing text, which is a line only when it is not empty.
    """
    pieces = _LINE_BREAK.split(text)
    lines = [pieces[i] + pieces[i + 1] for i in range(0, len(pieces) - 1, 2)]
    if pieces[-1]:
        lines.append(pieces[-1])
    return lines


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _log_keyword_lines(tree: ast.AST, lines: list[str]) -> set[int]:
    """1-based line numbers holding a whole-line ``log=`` keyword of an Output call."""
    drop: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and OUTPUT_CALLEE.match(_callee_name(node.func))):
            continue
        for keyword in node.keywords:
            if keyword.arg != "log":
                continue
            first, last = keyword.lineno, keyword.end_lineno or keyword.lineno
            head = lines[first - 1].encode("utf-8")[: keyword.col_offset]
            tail = lines[last - 1].encode("utf-8")[keyword.end_col_offset or 0 :]
            if head.strip() or tail.strip() != b",":
                raise SystemExit(
                    f"rule 7: log= at script line {first} is not a whole-line keyword; "
                    "refusing to vendor"
                )
            drop.update(range(first, last + 1))
    return drop


def strip_output_log(text: str) -> str:
    """Rule 7: delete every whole-line ``log=`` keyword of an ``*Output(`` call."""
    if not _LOG_KEYWORD.search(text):
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        if "Output(" in text:
            raise SystemExit(
                "rule 7: a string holding Output( and log= does not parse; refusing to vendor"
            ) from None
        return text
    lines = split_lines(text)
    drop = _log_keyword_lines(tree, lines)
    return "".join(line for number, line in enumerate(lines, 1) if number not in drop)


def rewrite_text(text: str, stem: str, rules: Iterable[int] = RULES) -> str:
    """Apply the numbered rules to one string of bundle content."""
    rules = frozenset(rules)
    if 2 in rules:
        text = re.sub(r"\bperceivo\.Layer(Input|Output)\b", r"perceivo.primitives.Fsm\1", text)
    if 1 in rules:
        text = re.sub(r"\bperceivo\.(" + _PRIMITIVE + r")\b", r"perceivo.primitives.\1", text)
    if 3 in rules and stem == "parallel-executor":
        text = text.replace('\n    fail_fast = failure_strategy == "fail_fast"\n', "\n")
        text = re.sub(r"\n[ \t]*fail_fast=fail_fast,", "", text)
    if 5 in rules and stem == "error-handler":
        text = text.replace(RULE5_RETURN_OLD, RULE5_RETURN_NEW)
        text = text.replace(RULE5_DOC_OLD, RULE5_DOC_NEW)
        for old, new in RULE5_PROSE:
            text = text.replace(old, new)
    if 6 in rules:
        text = _RULE6.sub("", text)
    if 7 in rules:
        text = strip_output_log(text)
    if 8 in rules:
        text = describe_repositories(text)
    if 9 in rules and stem == "error-handler":
        text = _RULE9.sub("", text)
    return text


def rewrite_bundle(value: Any, stem: str, rules: Iterable[int] = RULES) -> Any:
    """Apply :func:`rewrite_text` to every string in a parsed bundle."""
    rules = tuple(rules)
    if isinstance(value, str):
        return rewrite_text(value, stem, rules)
    if isinstance(value, list):
        return [rewrite_bundle(item, stem, rules) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_bundle(item, stem, rules) for key, item in value.items()}
    return value


def serialise(bundle: Any) -> bytes:
    """The bytes the platform writes for a bundle."""
    return (json.dumps(bundle, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


#: What ``--commit`` (or ``SOURCE.json``'s ``source_commit``) may name: an
#: object id or a ref name such as ``origin/dev``. The first character is
#: alphanumeric, so git can never read the value as an option, and no ``:``,
#: ``^``, ``@``, ``~`` or whitespace can reach a revision expression.
COMMIT_ARGUMENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
#: What ``rev-parse`` must answer with: one full object id (SHA-1 or SHA-256).
OBJECT_ID = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
#: A repository path this script asks git for: relative, no option shape, no
#: ``:``. A ``..`` component is refused separately. ``re.ASCII`` keeps ``\w`` to
#: ``[A-Za-z0-9_]``: without it ``\w`` also admits every Unicode letter and digit.
REPOSITORY_PATH = re.compile(r"\A\w[\w./-]{0,299}\Z", re.ASCII)


def checkout(repo: Path | str) -> str:
    """The platform checkout as a canonical directory that git treats as a work tree.

    Git receives it as the child process's working directory, never as an
    argument, so no value of ``repo`` can become a git option.
    """
    resolved = os.path.realpath(repo)
    if not os.path.isdir(resolved) or not os.path.exists(os.path.join(resolved, ".git")):
        raise SystemExit(f"{repo}: not a git checkout; refusing to vendor")
    return resolved


def checked_commit(commit: str) -> str:
    """``commit`` when it is an object id or plain ref name; refuses anything else."""
    if not COMMIT_ARGUMENT.match(commit) or ".." in commit:
        raise SystemExit(f"commit {commit!r} is not an object id or ref name; refusing to vendor")
    return commit


def checked_object_id(object_id: str) -> str:
    """``object_id`` when it is one full lower-case object id; refuses anything else."""
    if not OBJECT_ID.match(object_id):
        raise SystemExit(f"{object_id!r} is not a full git object id; refusing to vendor")
    return object_id


def checked_path(path: str) -> str:
    """``path`` when it is a plain relative repository path; refuses anything else."""
    if not REPOSITORY_PATH.match(path) or ".." in path.split("/"):
        raise SystemExit(f"{path!r} is not a plain repository path; refusing to vendor")
    return path


def _git(repo: str, args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def resolve_commit(repo: Path | str, commit: str) -> str:
    """The full object id ``commit`` names in the platform checkout."""
    commit = checked_commit(commit)
    answer = _git(checkout(repo), ["rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"])
    return checked_object_id(answer.strip())


def _ls_tree(repo: Path | str, commit: str, path: str) -> list[str]:
    commit, path = checked_object_id(commit), checked_path(path)
    return _git(checkout(repo), ["ls-tree", "-r", "--name-only", commit, "--", path]).split()


def _show(repo: Path | str, commit: str, path: str) -> bytes:
    commit, path = checked_object_id(commit), checked_path(path)
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=checkout(repo), check=True, capture_output=True
    ).stdout


def _assigned_literals(source: str) -> dict[str, Any]:
    """Module-level ``NAME = <literal>`` / ``NAME: T = <literal>`` values."""
    found: dict[str, Any] = {}
    for node in ast.parse(source).body:
        target, value = None, None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and value is not None:
            try:
                found[target.id] = ast.literal_eval(value)
            except ValueError:
                continue
    return found


def asset_ids(repo: Path, commit: str) -> dict[str, str]:
    """``stem -> library asset id``, derived as the platform catalog derives it.

    ``_uid(slot, kind, idx)`` in the platform's walker-id module: components are
    ``_uid(COMPONENT_SLOT, _K_CASSET, index)``, orchestrations are
    ``orch_id(slot) == _uid(slot, _K_ORCH, 1)``.
    """
    paths = upstream()
    catalog = _assigned_literals(_show(repo, commit, paths["catalog"]).decode("utf-8"))
    kinds = _assigned_literals(_show(repo, commit, paths["walker_ids"]).decode("utf-8"))

    def uid(slot: int, kind: int, idx: int) -> str:
        return f"a0000000-0000-{slot:04x}-{kind:04x}-{idx:012x}"

    ids = {
        stem: uid(catalog["COMPONENT_SLOT"], kinds["_K_CASSET"], index)
        for stem, index in catalog["COMPONENT_INDEX"].items()
    }
    ids.update(
        {
            stem: uid(slot, kinds["_K_ORCH"], 1)
            for stem, slot in catalog["ORCHESTRATION_SLOTS"].items()
        }
    )
    return ids


def derive(repo: Path, commit: str) -> dict[str, bytes]:
    """``published file name -> bytes`` for every vendored file, SOURCE.json included."""
    commit = resolve_commit(repo, commit)
    ids = asset_ids(repo, commit)
    listing = _ls_tree(repo, commit, upstream()["bundles"])
    paths = sorted(p for p in listing if p.endswith((".cbcomponent.json", ".cborchestration.json")))
    files: dict[str, bytes] = {}
    entries: dict[str, dict[str, str]] = {}
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        # The suffix, not the first dot: a stem may carry one (``ai-foundry-gpt-5.2-agent``).
        stem, kind = name.removesuffix(".json").rsplit(".", 1)
        source = _show(repo, commit, path)
        if serialise(json.loads(source)) != source:
            raise SystemExit(
                f"{path}: not in the platform's canonical serialisation; refusing to vendor"
            )
        if 9 in RULES and stem == "error-handler":
            hits = rule9_hits(json.loads(source))
            if hits != 1:
                raise SystemExit(f"{path}: rule 9's clause occurs {hits} times, not once")
        vendored = serialise(rewrite_bundle(json.loads(source), stem))
        source_text = source.decode("utf-8")
        for hit in _RULE6.finditer(source_text):
            line = source_text.count("\n", 0, hit.start()) + 1
            print(f"rule 6: {path}:{line} {hit.group().strip()} -- re-check it is a comment")
        if re.search(rb"\bTICK-\d+\b", vendored):
            raise SystemExit(f"{path}: a private ticket id survives the rewrite")
        refuse_repository_names(path, vendored)
        files[name] = vendored
        entries[stem] = {
            "file": name,
            "kind": "component" if kind == "cbcomponent" else "orchestration",
            "asset_id": ids[stem],
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "vendored_sha256": hashlib.sha256(vendored).hexdigest(),
        }
    if set(entries) != set(ids):
        raise SystemExit(f"bundles {sorted(entries)} do not match the catalog {sorted(ids)}")
    record = {
        "_comment": (
            "GENERATED by scripts/vendor_template_library.py -- do not hand-edit. "
            "Verify with: python scripts/vendor_template_library.py <platform checkout> --check"
        ),
        "rules": list(RULES),
        "entries": dict(sorted(entries.items())),
    }
    files["SOURCE.json"] = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("checkout", type=Path, help="a platform data service checkout")
    parser.add_argument("--commit", default="origin/dev", help="platform commit to vendor from")
    parser.add_argument("--check", action="store_true", help="byte-compare, write nothing")
    args = parser.parse_args(argv)

    if args.check:
        commit = upstream()["source_commit"]
        expected = derive(args.checkout, commit)
        committed = {p.name: p.read_bytes() for p in LIBRARY.iterdir() if p.is_file()}
        problems = [
            f"missing or different: {n}" for n in expected if committed.get(n) != expected[n]
        ]
        problems += [f"unexpected file: {n}" for n in committed if n not in expected]
        for line in problems:
            print(line)
        print(
            f"checked {len(expected)} files against platform {commit}: "
            f"{'OK' if not problems else f'{len(problems)} problem(s)'}"
        )
        return 1 if problems else 0

    commit = resolve_commit(args.checkout, args.commit)
    files = derive(args.checkout, commit)
    LIBRARY.mkdir(parents=True, exist_ok=True)
    for stale in LIBRARY.iterdir():
        if stale.is_file() and stale.name not in files:
            stale.unlink()
    for name, data in files.items():
        (LIBRARY / name).write_bytes(data)
    record = {**upstream(), "source_commit": commit}
    UPSTREAM_JSON.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(f"wrote {len(files)} files to {LIBRARY} from platform {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
