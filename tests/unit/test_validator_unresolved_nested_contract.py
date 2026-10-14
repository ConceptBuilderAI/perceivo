"""An edge into a nested orchestration not resolved locally has unknown fields.

The platform generates a nested orchestration node's ports from the child it
nests, so without the child locally the target port's response fields are
unknown, not absent: field coverage on such an edge is a warning, never an
error. The resolved end is still checked in full, so a schema there that is not
a field mapping is an error, as the platform reports it on every edge.

The fixture is the Conditional Router of
``test_validator_invoke_reverse_artifact``; its branch slots here require an
``answer`` response field that neither member's END declares.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from perceivo._bundle._node_refs import capability_members, embed_member_refs
from perceivo._bundle.capability import capability_to_project
from perceivo._bundle.orchestration import bundle_to_orchestration
from perceivo.validator._orchestration import validate_orchestration

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "orchestration_classifier_router_invoke_reverse.json"
)
_MISSING = (
    "Required field 'answer' in source response_data_schema is not provided by target "
    "response_data_schema."
)


_REQUIRES_ANSWER: dict[str, Any] = {"answer": {"type": "string", "required": True}}


def _fixture(branch_schema: Any) -> dict[str, Any]:
    fixture: dict[str, Any] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    (fsm,) = [c for c in fixture["router"]["components"].values() if c["category"] == "FSM"]
    branches = [p for p in fsm["ports"] if p["key"] in ("branch_1", "branch_2")]
    assert len(branches) == 2
    for port in branches:
        port["response_data_schema"] = copy.deepcopy(branch_schema)
    return fixture


def _project(tmp_path: Path, *, members: bool, branch_schema: Any = _REQUIRES_ANSWER) -> Path:
    fixture = _fixture(branch_schema)
    path = bundle_to_orchestration(copy.deepcopy(fixture["router"]), tmp_path / "router")
    if members:
        capability = capability_to_project(
            {
                "export_type": "capability_bundle",
                "capability": {"name": "Company Policy Chatbot"},
                "members": fixture["members"],
            },
            tmp_path / "capability",
        )
        assert len(embed_member_refs(path, capability_members(capability))) == 2
    return path


def test_unresolved_members_warn_instead_of_failing_the_contract(tmp_path):
    report = validate_orchestration(str(_project(tmp_path, members=False)))

    assert not [c.message for c in report.checks if c.id.startswith("ORCH-CONTRACT-0")]
    warnings = [c for c in report.checks if c.id.startswith("ORCH-CONTRACT-UNVERIFIED")]
    assert [(c.severity, c.title) for c in warnings] == [
        ("warning", "Port contract not verifiable"),
    ] * 2
    assert sorted(c.message.split("'")[1] for c in warnings) == ["node_4", "node_6"]
    assert report.valid


def test_resolved_members_still_fail_a_missing_required_field(tmp_path):
    report = validate_orchestration(str(_project(tmp_path, members=True)))

    contract = [c for c in report.checks if c.id.startswith("ORCH-CONTRACT")]
    assert [(c.severity, c.message) for c in contract] == [("error", _MISSING)] * 2
    assert not report.valid


def test_unresolved_members_still_fail_a_malformed_schema_on_the_resolved_end(tmp_path):
    report = validate_orchestration(
        str(_project(tmp_path, members=False, branch_schema=["answer"]))
    )

    contract = [c for c in report.checks if c.id.startswith("ORCH-CONTRACT-0")]
    assert [(c.severity, c.message) for c in contract] == [
        (
            "error",
            "Invalid source response_data_schema: schema must be a field mapping, found list.",
        )
    ] * 2
    assert not report.valid
