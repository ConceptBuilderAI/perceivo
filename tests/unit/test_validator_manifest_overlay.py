"""The orchestration validator checks the ports the platform checks.

Two divergences failed unmodified seeds the platform accepts:

* the platform overlays each component manifest onto the port rows before any
  edge is checked; the SDK read the ``orchestration.yaml`` mirror, which can be
  stale;
* the platform judges port-count rules on the port ROWS; the SDK judged the
  manifest's port sections, which may declare more.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from perceivo.validator._orchestration import validate_orchestration
from perceivo.validator._orchestration._component_checks import component_checks
from perceivo.validator._orchestration._loader import _load_from_directory
from perceivo.validator._orchestration._manifest_overlay import (
    overlay_manifest_ports,
    parse_data_fields,
)

_POLICY_MANIFEST = """
name: P
input_ports:
- port_name: input
  port_type: in
  artifact_type_input: {atype}
"""


def _policy_project(tmp_path: Path, manifest_atype: str | None) -> Path:
    """An FSM whose policy port (artifact ``none``) INVOKEs a POLICY.

    The mirror says the POLICY's input takes ``json``, which ``none`` cannot
    feed. ``manifest_atype`` is what the POLICY's manifest declares.
    """
    ports = [
        {
            "id": "f_pol",
            "node_id": "f",
            "name": "policy",
            "key": "policy",
            "port_type": "policy",
            "direction": "out",
            "artifact_type_output": "none",
        },
        {
            "id": "p_in",
            "node_id": "p",
            "name": "input",
            "key": "input",
            "port_type": "in",
            "direction": "in",
            "artifact_type_input": "json",
        },
    ]
    nodes: list[dict[str, Any]] = [
        {"id": "f", "name": "f", "component_type": "FSM"},
        {"id": "p", "name": "p", "component_type": "POLICY", "script": "p.py"},
    ]
    if manifest_atype is not None:
        nodes[1]["manifest"] = "p.yaml"
        (tmp_path / "p.yaml").write_text(_POLICY_MANIFEST.format(atype=manifest_atype), "utf-8")
    graph = {
        "nodes": nodes,
        "ports": ports,
        "edges": [{"source_port_id": "f_pol", "target_port_id": "p_in", "kind": "INVOKE"}],
    }
    (tmp_path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    (tmp_path / "p.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _artifact_errors(project: Path) -> list[str]:
    report = validate_orchestration(str(project), validate_scripts=False)
    return [c.message for c in report.errors if c.title == "Artifact type mismatch"]


def test_the_stale_mirror_alone_is_an_artifact_mismatch(tmp_path: Path) -> None:
    """Control: without a manifest the mirror is all there is, and it mismatches."""
    assert _artifact_errors(_policy_project(tmp_path, None))


def test_the_manifest_artifact_type_overrides_the_mirror(tmp_path: Path) -> None:
    assert _artifact_errors(_policy_project(tmp_path, "none")) == []


def test_a_manifest_that_agrees_with_the_mirror_still_fails(tmp_path: Path) -> None:
    """The overlay follows the manifest; it does not simply silence the check."""
    assert _artifact_errors(_policy_project(tmp_path, "json"))


def test_string_field_specs_parse_as_the_server_parses_them() -> None:
    fields = parse_data_fields(
        {
            "a": "string (optional) - The a.",
            "b": "integer",
            "c": {"type": "float", "required": False},
            "d": 3,
        }
    )
    assert fields == {
        "a": {"type": "string", "required": False, "description": "The a."},
        "b": {"type": "integer", "required": True, "description": ""},
        "c": {"type": "float", "required": False, "description": ""},
    }
    assert parse_data_fields(
        [{"name": "x", "type": "boolean", "optional": True}, {"type": "s"}]
    ) == {"x": {"type": "boolean", "required": False, "description": ""}}
    assert parse_data_fields("nope") == {}


def test_legacy_spellings_are_carried_as_authored_never_rewritten() -> None:
    # There is no alias map: the spelling reaches the port unchanged, where the
    # strict checks (MAN-PROPTYPE, ORCH-PROPTYPE) report it.
    fields = parse_data_fields({"b": "int", "c": {"type": " number "}})
    assert fields["b"]["type"] == "int"
    assert fields["c"]["type"] == "number"
    assert parse_data_fields([{"name": "x", "type": "bool"}])["x"]["type"] == "bool"


def test_overlay_sets_schemas_and_contract_and_skips_other_nodes() -> None:
    manifest = """
output_ports:
- port_name: out
  request_data:
    q: string - The question.
  response_data:
    r: object (optional)
  artifact_output_schema:
    k: string
"""
    mine: dict[str, Any] = {"node_id": "n", "key": "out", "request_data_schema": {"stale": {}}}
    other = {"node_id": "m", "key": "out"}
    overlay_manifest_ports(manifest, "n", [mine, other, "junk"])  # type: ignore[list-item]
    assert mine["request_data_schema"] == {
        "q": {"type": "string", "required": True, "description": "The question."}
    }
    assert mine["contract"]["required"] == ["q"]
    assert mine["post_tool_contract"] == {
        "type": "object",
        "properties": {"r": {"type": "object", "description": ""}},
    }
    assert mine["artifact_output_schema"] == {"k": {"type": "string"}}
    assert other == {"node_id": "m", "key": "out"}


def test_overlay_ignores_an_unparseable_or_portless_manifest() -> None:
    port = {"node_id": "n", "key": "input", "artifact_type_input": "json"}
    for manifest in ("", ": : :\n  - [", "just a string", "input_ports: nope"):
        overlay_manifest_ports(manifest, "n", [port])
    # Only the direction is stated: the fold and the dataflow chain read it.
    assert port == {
        "node_id": "n",
        "key": "input",
        "artifact_type_input": "json",
        "direction": "in",
    }


# --------------------------------------------------------------------------- #
# port-count rules judge the rows                                              #
# --------------------------------------------------------------------------- #

_TOOL = """
import perceivo

ENTRY_CLASS = "T"


class T(perceivo.primitives.Tool):
    async def run(self, input: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
        return perceivo.primitives.ToolOutput(response_data={})
"""

# The seed tools' shape: the manifest declares an ``output`` port no row has.
_TOOL_MANIFEST = """
name: T
extension_type: tool
input_ports:
- port_name: input
  port_type: in
output_ports:
- port_name: output
  port_type: out
"""


def _tool_checks(rows: list[dict[str, Any]] | None) -> list[str]:
    comp = {
        "name": "T",
        "component_type": "TOOL",
        "script": _TOOL,
        "manifest_yaml": _TOOL_MANIFEST,
        "ports": rows,
    }
    return [c.id for c in component_checks({"components": [comp]}, True) if c.severity == "error"]


def test_the_manifest_alone_breaks_the_tool_rule() -> None:
    """Control: judged on the manifest, the extra OUT port is a finding."""
    assert _tool_checks(None) == ["T/PORT-RULE-001"]


def test_the_rows_decide_the_port_rules() -> None:
    assert _tool_checks([{"key": "input", "port_type": "in", "direction": "IN"}]) == []
    assert _tool_checks(
        [
            {"key": "input", "port_type": "in", "direction": "IN"},
            {"key": "output", "port_type": "out", "direction": "OUT"},
        ]
    ) == ["T/PORT-RULE-001"]


def test_the_loader_hands_each_component_its_bundled_ports(tmp_path: Path) -> None:
    """A component with no manifest bundles the ports its rows declare, and none
    when it has no row."""
    graph = {
        "nodes": [
            {"id": "t", "name": "T", "component_type": "TOOL", "script": "t.py"},
            {"id": "u", "name": "U", "component_type": "TOOL", "script": "u.py"},
        ],
        "ports": [
            {"id": "t_in", "node_id": "t", "name": "input", "port_type": "in", "direction": "in"},
            {"id": "v", "node_id": "t", "name": "output", "port_type": "out", "virtual": True},
        ],
        "edges": [],
    }
    (tmp_path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    for name in ("t.py", "u.py"):
        (tmp_path / name).write_text(_TOOL, encoding="utf-8")

    loaded = _load_from_directory(str(tmp_path))

    assert loaded is not None
    rows = {c["name"]: c["ports"] for c in loaded["components"]}
    assert rows == {"T": [{"key": "input", "port_type": "in", "direction": "IN"}], "U": []}
