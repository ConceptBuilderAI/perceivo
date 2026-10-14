"""A mainflow AGENT's translated OUT port leaves the bundle, contract and all.

``perceivo new orchestration`` wires ``agent.output -> end.input`` with a
MAINFLOW edge. The platform refuses a MAINFLOW edge out of an AGENT's ``out``
port, so the writer moves the hop into ``config.mainflow_target_node_ref`` and
drops the edge. Leaving the ``output`` port on the component would make the
platform, which creates a port for every component port and requires each to be
connected, refuse the upload: "Port 'output' on node 'agent' has no connected
edge".

These tests pin the bundle shape, the retirement's safety limits, and the local
check that reports an unconnected bundled port before upload.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle._mainflow_ports import CONTRACT_CONFLICT, retire_translated_out_ports
from perceivo._bundle._port_consistency import TerminalPortShapeError
from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._bundle.schema import ComponentExportData, PortExportData, PortRefExportData
from perceivo._cli import main as perceivo_cli
from perceivo.orchestration import publish
from perceivo.validator import validate_orchestration
from perceivo.validator._orchestration._bundle_connections import unconnected_port_checks


def _scaffold(tmp_path: Path, name: str = "proj") -> Path:
    result = CliRunner().invoke(
        perceivo_cli, ["new", "orchestration", name, "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    return tmp_path / name


def _by_category(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["category"]: c for c in bundle["components"].values()}


def _node(bundle: dict[str, Any], local_id: str) -> dict[str, Any]:
    return next(n for n in bundle["nodes"] if n["local_id"] == local_id)


# ---------------------------------------------------------------------------
# The scaffold, end to end
# ---------------------------------------------------------------------------


async def test_scaffold_bundle_has_no_unconnected_port(tmp_path: Path) -> None:
    bundle = (await publish(str(_scaffold(tmp_path)), name="scaffold", dry_run=True)).bundle
    assert unconnected_port_checks(bundle) == []


async def test_scaffold_agent_carries_its_contract_on_the_in_port(tmp_path: Path) -> None:
    bundle = (await publish(str(_scaffold(tmp_path)), name="scaffold", dry_run=True)).bundle
    agent = _by_category(bundle)["AGENT"]
    assert [(p["key"], p["direction"]) for p in agent["ports"]] == [("input", "IN")]
    in_port = agent["ports"][0]
    assert set(in_port["response_data_schema"]) == {"answer", "result"}
    assert in_port["request_data_schema"] is not None
    assert in_port["artifact_type_output"] == "json"
    assert _node(bundle, "node_agent")["config"]["mainflow_target_node_ref"] == "node_end"


def test_scaffold_files_still_wire_the_agent_output(tmp_path: Path) -> None:
    """The local graph is unchanged: the translation happens only in the bundle."""
    project = _scaffold(tmp_path)
    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    edges = {(e["source_port_id"], e["target_port_id"]) for e in data["edges"]}
    assert ("port_node_agent_output", "port_node_end_input") in edges
    assert "output_ports" in yaml.safe_load((project / "agent.yaml").read_text(encoding="utf-8"))


def test_scaffold_validates_including_bundle_connectivity(tmp_path: Path) -> None:
    report = validate_orchestration(_scaffold(tmp_path))
    assert report.valid, [c.message for c in report.errors]


def test_a_renamed_external_port_refuses_the_bundle(tmp_path: Path) -> None:
    """A START node port named apart from its manifest's is a second port, not a rename."""
    project = _scaffold(tmp_path)
    path = project / "orchestration.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count("name: external_input") == 1
    path.write_text(text.replace("name: external_input", "name: trigger"), encoding="utf-8")
    refusal = "START must have exactly 1 EXTERNAL_IN port (found 2)"
    with pytest.raises(TerminalPortShapeError, match=re.escape(refusal)):
        orchestration_to_bundle(project)


# ---------------------------------------------------------------------------
# A genuinely unconnected port is reported locally (control: this must fail)
# ---------------------------------------------------------------------------


@pytest.fixture
def unwired_project(tmp_path: Path) -> Path:
    """The scaffold with a second AGENT out port that no edge ever wires."""
    project = _scaffold(tmp_path, "unwired")
    path = project / "agent.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["output_ports"].append(
        {"port_name": "extra", "port_type": "out", "artifact_type_output": "json"}
    )
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return project


def test_unwired_port_is_in_the_bundle(unwired_project: Path) -> None:
    bundle = orchestration_to_bundle(unwired_project)
    messages = [c.message for c in unconnected_port_checks(bundle)]
    assert len(messages) == 1, messages
    assert messages[0].startswith("Port 'extra' on node 'agent' has no connected edge")


def test_unwired_port_fails_validation(unwired_project: Path) -> None:
    report = validate_orchestration(unwired_project, validate_scripts=False)
    found = [c for c in report.errors if c.id.startswith("ORCH-BUNDLE-UNCONNECTED-")]
    assert not report.valid
    assert len(found) == 1, [c.message for c in report.errors]
    assert "'extra'" in found[0].message


# ---------------------------------------------------------------------------
# retire_translated_out_ports: its limits
# ---------------------------------------------------------------------------


def _port(key: str, direction: str, port_type: str, **fields: Any) -> PortExportData:
    return PortExportData(key=key, direction=direction, port_type=port_type, **fields)


def _agent(*ports: PortExportData) -> ComponentExportData:
    return ComponentExportData(name="agent", category="AGENT", ports=list(ports))


_IN = _port("input", "IN", "in", request_data_schema={"q": {"type": "string"}})
_OUT = _port(
    "output",
    "OUT",
    "out",
    response_data_schema={"result": {"type": "string"}},
    artifact_type_output="json",
    response_data_contract="dynamic",
)
_RAW = [{"id": "p_out", "node_id": "n", "name": "output", "port_type": "out"}]


def _run(
    component: ComponentExportData,
    *,
    raw: list[dict[str, Any]] | None = None,
    translated: set[str] | None = None,
    exported: list[PortRefExportData] | None = None,
) -> ComponentExportData:
    components = {"comp_0": component}
    retire_translated_out_ports(
        components,
        {"n": "comp_0", "m": "comp_0"},
        _RAW if raw is None else raw,
        {"p_out"} if translated is None else translated,
        exported or [],
    )
    return components["comp_0"]


def test_retire_removes_the_port_and_folds_the_response_contract() -> None:
    result = _run(_agent(_IN, _OUT))
    assert [p.key for p in result.ports] == ["input"]
    folded = result.ports[0]
    assert folded.request_data_schema == {"q": {"type": "string"}}
    assert folded.response_data_schema == {"result": {"type": "string"}}
    assert folded.artifact_type_output == "json"
    assert folded.response_data_contract == "dynamic"


def test_retire_accepts_an_in_port_that_states_the_same_contract() -> None:
    in_port = _IN.model_copy(update={"response_data_schema": {"result": {"type": "string"}}})
    result = _run(_agent(in_port, _OUT))
    assert result.ports[0].response_data_schema == {"result": {"type": "string"}}
    assert result.ports[0].artifact_type_output == "json"
    assert in_port.artifact_type_output is None, "the caller's port was mutated"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("response_data_schema", {"own": {"type": "string"}}, id="schema"),
        pytest.param("artifact_type_output", "text", id="artifact-type"),
    ],
)
def test_retire_refuses_a_conflicting_contract(field: str, value: Any) -> None:
    in_port = _IN.model_copy(update={field: value})
    with pytest.raises(BundleConfigError) as caught:
        _run(_agent(in_port, _OUT))
    assert CONTRACT_CONFLICT in caught.value.__notes__
    message = str(caught.value)
    assert f"different {field} on its IN port 'input'" in message
    assert "OUT port 'output'" in message


def test_retire_keeps_a_port_another_row_still_references() -> None:
    component = _agent(_IN, _OUT)
    exported = [
        PortRefExportData(node_ref="m", component_port_key="output", component_port_direction="OUT")
    ]
    assert _run(component, exported=exported) is component


@pytest.mark.parametrize(
    ("component", "raw", "translated"),
    [
        pytest.param(_agent(_OUT), None, None, id="no-in-port"),
        pytest.param(_agent(_IN, _port("second", "IN", "in"), _OUT), None, None, id="two-in-ports"),
        pytest.param(_agent(_IN, _port("output", "OUT", "tool")), None, None, id="not-an-out-port"),
        pytest.param(_agent(_IN), None, None, id="component-lacks-the-port"),
        pytest.param(_agent(_IN, _OUT), None, set(), id="not-translated"),
        pytest.param(
            _agent(_IN, _OUT), [{**_RAW[0], "virtual": True}], None, id="synthesized-port"
        ),
        pytest.param(
            _agent(_IN, _OUT), [{**_RAW[0], "node_id": "elsewhere"}], None, id="unknown-node"
        ),
    ],
)
def test_retire_leaves_unsafe_cases_alone(
    component: ComponentExportData,
    raw: list[dict[str, Any]] | None,
    translated: set[str] | None,
) -> None:
    assert _run(component, raw=raw, translated=translated) is component


# ---------------------------------------------------------------------------
# unconnected_port_checks: the platform's exemptions, and nothing more
# ---------------------------------------------------------------------------


def _bundle(ports: list[dict[str, Any]], *, config: dict[str, Any] | None = None, **node: Any):
    return {
        "components": {"c": {"name": "c", "category": "AGENT", "ports": ports}},
        "nodes": [
            {"local_id": "a", "name": "a", "component_ref": "c", "config": config, **node},
            {"local_id": "b", "name": "b", "component_ref": "", "config": None},
        ],
        "ports": [],
        "edges": [],
    }


def _p(key: str, direction: str, port_type: str) -> dict[str, Any]:
    return {"key": key, "direction": direction, "port_type": port_type}


@pytest.mark.parametrize(
    ("ports", "config", "extra"),
    [
        pytest.param([_p("x", "IN", "external_in")], None, {}, id="external-in"),
        pytest.param([_p("x", "OUT", "external_out")], None, {}, id="external-out"),
        pytest.param([_p("t", "IN", "tool")], {"is_agent_tool": True}, {}, id="combo-tool-in"),
        pytest.param([_p("s", "OUT", "state")], None, {}, id="state-without-row"),
        pytest.param([_p("p", "OUT", "policy")], None, {}, id="policy-without-row"),
        pytest.param([_p("o", "OUT", "out")], None, {"orchestration_ref": "orch_0"}, id="nested"),
    ],
)
def test_exempt_ports_are_not_reported(
    ports: list[dict[str, Any]], config: dict[str, Any] | None, extra: dict[str, Any]
) -> None:
    assert unconnected_port_checks(_bundle(ports, config=config, **extra)) == []


def test_in_port_on_a_mainflow_target_is_exempt() -> None:
    bundle = _bundle([_p("input", "IN", "in")])
    bundle["nodes"][1]["config"] = {"mainflow_target_node_ref": "a"}
    assert unconnected_port_checks(bundle) == []


@pytest.mark.parametrize(
    ("port", "config"),
    [
        pytest.param(_p("o", "OUT", "out"), {"is_agent_tool": True}, id="out-never-exempt"),
        pytest.param(_p("input", "IN", "in"), None, id="in-not-a-target"),
        pytest.param(_p("t", "IN", "tool"), None, id="tool-in-not-combo"),
    ],
)
def test_unconnected_ports_are_reported(
    port: dict[str, Any], config: dict[str, Any] | None
) -> None:
    checks = unconnected_port_checks(_bundle([port], config=config))
    assert [c.id for c in checks] == ["ORCH-BUNDLE-UNCONNECTED-001"]
    assert checks[0].severity == "error"


def test_a_state_port_with_a_row_but_no_edge_is_reported() -> None:
    bundle = _bundle([_p("s", "OUT", "state")])
    bundle["ports"] = [
        {"node_ref": "a", "component_port_key": "s", "component_port_direction": "OUT"}
    ]
    assert len(unconnected_port_checks(bundle)) == 1


def test_an_edge_connects_both_of_its_rows() -> None:
    bundle = _bundle([_p("o", "OUT", "out"), _p("i", "IN", "in")])
    bundle["ports"] = [
        {"node_ref": "a", "component_port_key": "o", "component_port_direction": "OUT"},
        {"node_ref": "a", "component_port_key": "i", "component_port_direction": "IN"},
    ]
    bundle["edges"] = [
        {"source_port_index": 0, "target_port_index": 1},
        {"source_port_index": 7, "target_port_index": None},
    ]
    assert unconnected_port_checks(bundle) == []
