"""The platform's artifact checks, run by the SDK validator too.

The platform refuses the v4 Execution Graph Classifier Router on import with an
INVOKE reverse artifact mismatch (the target returns ``text``, the source expects
``json``), which a check of only the forward pair, and only when both ports
declare a type, does not see. The platform
also blocks a target that requires an
artifact the source never declares, and on INVOKE edges checks the return leg:
the invoked target's ``artifact_type_output`` must satisfy the caller's
``artifact_type_input``.

The fixture is the router's Conditional Router bundle, verbatim, with
its two ``@member:`` children reduced to START -> LLM Response Adapter -> END.
The END's input port (``artifact_type_input: text``) is what the platform
stamps on the branch node as its ``artifact_type_output``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from perceivo._bundle._node_refs import capability_members, embed_member_refs
from perceivo._bundle.capability import capability_to_project
from perceivo._bundle.orchestration import bundle_to_orchestration
from perceivo.validator._contracts import validate_artifact_constraints
from perceivo.validator._orchestration import validate_orchestration

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "orchestration_classifier_router_invoke_reverse.json"
)
_REVERSE = "INVOKE reverse artifact mismatch: target returns 'text', source expects 'json'"
_BRANCH = "- port_name: {}\n  artifact_type_input: {}\n"


def _load() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return loaded


def _router_fsm(router: dict[str, Any]) -> dict[str, Any]:
    fsms: list[dict[str, Any]] = [
        c for c in router["components"].values() if c["category"] == "FSM"
    ]
    (fsm,) = fsms
    return fsm


def _with_branch_input(fixture: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    """The fix under test: the branch ports declare ``artifact_type_input`` in the manifest."""
    fixture = copy.deepcopy(fixture)
    fsm = _router_fsm(fixture["router"])
    for branch in ("branch_1", "branch_2"):
        before = _BRANCH.format(branch, "json")
        assert fsm["manifest_yaml"].count(before) == 1
        fsm["manifest_yaml"] = fsm["manifest_yaml"].replace(
            before, _BRANCH.format(branch, artifact_type)
        )
    return fixture


def _project(fixture: dict[str, Any], tmp_path: Path, *, members: bool) -> Path:
    path = bundle_to_orchestration(fixture["router"], tmp_path / "router")
    if members:
        capability = capability_to_project(
            {
                "export_type": "capability_bundle",
                "capability": {"name": "Company Policy Chatbot"},
                "members": fixture["members"],
            },
            tmp_path / "capability",
        )
        resolved = embed_member_refs(path, capability_members(capability))
        assert sorted(resolved) == [
            "Company Policy Chatbot - Plain",
            "Company Policy Chatbot - RAG",
        ]
    return path


def _errors(report) -> list[str]:
    return [c.message for c in report.checks if c.severity == "error"]


# -- the v4 router, as the platform validates it --------------------------------


def test_pre_fix_router_is_rejected_with_the_platform_message(tmp_path):
    report = validate_orchestration(str(_project(_load(), tmp_path, members=True)))

    assert not report.valid
    errors = _errors(report)
    assert len(errors) == 2, errors  # one per branch
    assert all(e.endswith(_REVERSE) for e in errors), errors
    assert all(c.title == "Artifact type mismatch" for c in report.checks if c.severity == "error")


def test_post_fix_router_is_accepted(tmp_path):
    fixture = _with_branch_input(_load(), "text")

    report = validate_orchestration(str(_project(fixture, tmp_path, members=True)))

    assert report.valid, _errors(report)
    assert not [c for c in report.checks if "UNVERIFIED" in c.id]


def test_unresolved_members_warn_instead_of_inventing_a_verdict(tmp_path):
    """Without its members the branch nodes' ports are unknown, not artifact-free."""
    report = validate_orchestration(str(_project(_load(), tmp_path, members=False)))

    assert report.valid, _errors(report)
    warnings = [c for c in report.checks if "ORCH-ARTIFACT-UNVERIFIED" in c.id]
    assert len(warnings) == 2
    assert all(c.severity == "warning" for c in warnings)


# -- the checks, one port pair at a time ----------------------------------------


@pytest.mark.parametrize(
    ("source", "target", "kind", "expected"),
    [
        pytest.param(
            {"artifact_type_input": "json"},
            {"artifact_type_output": "text"},
            "INVOKE",
            [_REVERSE],
            id="reverse-mismatch",
        ),
        pytest.param(
            {"artifact_type_input": "json"},
            {"artifact_type_output": "text"},
            "MAINFLOW",
            [],
            id="reverse-only-on-invoke",
        ),
        pytest.param(
            {"artifact_type_input": "json"},
            {"artifact_type_output": "optional(json)"},
            "INVOKE",
            [
                "INVOKE reverse artifact mismatch: target returns 'optional(json)', "
                "source expects 'json'"
            ],
            id="reverse-maybe-absent-return",
        ),
        pytest.param(
            {"artifact_type_input": "optional(json)"},
            {"artifact_type_output": "json"},
            "INVOKE",
            [],
            id="reverse-compatible",
        ),
        pytest.param(
            {"artifact_type_input": "json"},
            {},
            "INVOKE",
            [
                "INVOKE reverse: source expects artifact type 'json' back from target, "
                "but target has no artifact_type_output"
            ],
            id="reverse-target-declares-none",
        ),
        pytest.param(
            {"artifact_type_input": "none"},
            {},
            "INVOKE",
            [],
            id="reverse-source-expects-nothing",
        ),
        pytest.param(
            {},
            {"artifact_type_input": "text"},
            "MAINFLOW",
            [
                "Target port requires artifact type 'text' but source port does not "
                "specify an output artifact type"
            ],
            id="target-requires-source-silent",
        ),
        pytest.param(
            {},
            {"artifact_type_input": "optional(text)"},
            "MAINFLOW",
            [],
            id="optional-target-exempt",
        ),
        pytest.param(
            {"artifact_type_output": "text"},
            {"artifact_type_input": "none"},
            "MAINFLOW",
            [
                "Source port produces artifact type 'text' but target port expects no "
                "artifact (none)"
            ],
            id="target-expects-none",
        ),
        pytest.param(
            {"artifact_type_output": "json", "artifact_output_schema": {"a": {"type": "string"}}},
            {
                "artifact_type_input": "json",
                "artifact_input_schema": {"a": {"type": "float"}, "b": {"type": "string"}},
            },
            "MAINFLOW",
            [
                "Artifact schema type mismatch for field 'a': source has 'string', "
                "target expects 'float'",
                "Missing field in artifact schema: target expects 'b' but source does "
                "not provide it",
            ],
            id="json-artifact-schema",
        ),
        pytest.param(
            {"artifact_type_constraint": "TEXT"},
            {"artifact_type_constraint": "AUDIO"},
            "",
            ["Artifact type mismatch: source provides TEXT, target expects AUDIO"],
            id="legacy-constraint",
        ),
    ],
)
def test_artifact_checks_match_the_platform(source, target, kind, expected):
    assert validate_artifact_constraints(source, target, edge_kind=kind) == expected


# -- moved from test_cov_validators_core: the single-pair basics ----------------


def test_artifact_mismatch_errors():
    src = {"artifact_type_output": "IMAGE"}
    tgt = {"artifact_type_input": "TEXT"}
    errors = validate_artifact_constraints(src, tgt)
    assert errors == ["Port artifact type mismatch: source produces 'IMAGE', target expects 'TEXT'"]


def test_artifact_match_case_insensitive_no_error():
    src = {"artifact_type_output": "image"}
    tgt = {"artifact_type_input": "IMAGE"}
    assert validate_artifact_constraints(src, tgt) == []


def test_artifact_uses_constraint_fallback():
    src = {"artifact_type_constraint": "TEXT"}
    tgt = {"artifact_type_constraint": "AUDIO"}
    errors = validate_artifact_constraints(src, tgt)
    assert errors == ["Artifact type mismatch: source provides TEXT, target expects AUDIO"]


def test_artifact_missing_one_side_no_error():
    assert validate_artifact_constraints({"artifact_type_output": "IMAGE"}, {}) == []
    assert validate_artifact_constraints({}, {"artifact_type_input": "optional(IMAGE)"}) == []


def test_required_artifact_with_silent_source_errors():
    # The platform blocks a target that requires an artifact no source declares.
    assert validate_artifact_constraints({}, {"artifact_type_input": "IMAGE"}) == [
        "Target port requires artifact type 'IMAGE' but source port does not specify "
        "an output artifact type"
    ]


def test_required_artifact_satisfies_optional_consumer():
    src = {"artifact_type_output": "file"}
    tgt = {"artifact_type_input": "optional(file)"}
    assert validate_artifact_constraints(src, tgt) == []


def test_none_artifact_satisfies_optional_consumer():
    src = {"artifact_type_output": "none"}
    tgt = {"artifact_type_input": "optional(json)"}
    assert validate_artifact_constraints(src, tgt) == []


def test_optional_producer_does_not_satisfy_required_consumer():
    src = {"artifact_type_output": "optional(file)"}
    tgt = {"artifact_type_input": "file"}
    errors = validate_artifact_constraints(src, tgt)
    assert len(errors) == 1
    assert errors[0].startswith(
        "Port artifact type mismatch: source declares 'optional(file)', so it may emit"
    )
