"""The bundle connectivity check exempts exactly what the platform's importer does.

Each project below imports on the platform, so ``perceivo validate
orchestration`` must pass it. The exemptions come from the same derivation the
file-level check uses (``_ports._unwired_exempt_nodes``), so a mainflow target
named by any of its three spellings, and any port on a hidden
(``is_agent_tool_internal``) node, is exempt. Each must-pass case has a control
that differs only in the property under test and must fail, so a check that
exempted everything would not pass here.

It also pins the local refusal of a mainflow node whose IN and OUT ports declare
conflicting response contracts: the platform judges the IN port's, the author
wired the OUT port's, and the mismatch otherwise surfaces only on upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle._mainflow_ports import CONTRACT_CONFLICT
from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._cli import main as perceivo_cli
from perceivo.validator import validate_orchestration
from perceivo.validator._orchestration._bundle_connections import unconnected_port_checks


def _scaffold(tmp_path: Path, name: str) -> Path:
    result = CliRunner().invoke(
        perceivo_cli, ["new", "orchestration", name, "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    return tmp_path / name


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _save(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _unconnected(project: Path) -> list[str]:
    return [c.message for c in unconnected_port_checks(orchestration_to_bundle(project))]


def _config_hop(tmp_path: Path, name: str, config: dict[str, Any]) -> Path:
    """The scaffold in the platform's own shape: the AGENT has no OUT port, its
    contract is on IN, and its hop to END is carried by *config* alone, so END's
    IN port is unwired and exempt only if the check reads *config*."""
    project = _scaffold(tmp_path, name)
    manifest = _load(project / "agent.yaml")
    manifest["input_ports"][0]["response_data"] = manifest["output_ports"][0]["response_data"]
    manifest.pop("output_ports")
    _save(project / "agent.yaml", manifest)
    graph = _load(project / "orchestration.yaml")
    graph["ports"] = [p for p in graph["ports"] if p["id"] != "port_node_agent_output"]
    graph["edges"] = [e for e in graph["edges"] if e["source_port_id"] != "port_node_agent_output"]
    agent = next(n for n in graph["nodes"] if n["id"] == "node_agent")
    agent["config"] = config
    _save(project / "orchestration.yaml", graph)
    return project


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"mainflow_target_node_ref": "node_end"}, id="ref"),
        pytest.param({"mainflow_target_node_index": 2}, id="index"),
        pytest.param({"mainflow_target_node_index": "2"}, id="index-as-string"),
        pytest.param({"mainflow_target_node_id": "node_end"}, id="id-raw"),
    ],
)
def test_every_mainflow_target_spelling_exempts_the_target(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    project = _config_hop(tmp_path, "hop", config)
    assert _unconnected(project) == []
    report = validate_orchestration(project, validate_scripts=False)
    assert report.valid, [c.message for c in report.errors]


def _chain(tmp_path: Path, name: str, hop: dict[str, Any]) -> Path:
    """start -> agent, then agent -> agent2 by *hop*, then agent2 -> end by ref.

    agent2 is not an END, so nothing on the platform repairs its IN port: it is
    exempt only if *hop* names it. (An unwired END IN port is repaired on import
    whatever the hop says, so END cannot serve as the control.)
    """
    project = _config_hop(tmp_path, name, hop)
    graph = _load(project / "orchestration.yaml")
    graph["nodes"].insert(
        2,
        {
            "id": "node_agent2",
            "name": "agent2",
            "component_type": "AGENT",
            "script": "agent.py",
            "manifest": "agent.yaml",
            "config": {"mainflow_target_node_ref": "node_end"},
        },
    )
    graph["ports"].append(
        {
            "id": "port_node_agent2_input",
            "node_id": "node_agent2",
            "name": "input",
            "port_type": "in",
            "direction": "in",
        }
    )
    _save(project / "orchestration.yaml", graph)
    return project


@pytest.mark.parametrize(
    "hop",
    [
        pytest.param({"mainflow_target_node_ref": "node_agent2"}, id="ref"),
        pytest.param({"mainflow_target_node_index": 2}, id="index"),
    ],
)
def test_a_hop_naming_a_mid_chain_node_exempts_it(tmp_path: Path, hop: dict[str, Any]) -> None:
    assert _unconnected(_chain(tmp_path, "chain", hop)) == []


@pytest.mark.parametrize(
    "hop",
    [
        pytest.param({"mainflow_target_node_index": 0}, id="index-names-another-node"),
        pytest.param({}, id="no-hop"),
    ],
)
def test_a_hop_that_does_not_name_the_node_exempts_nothing(
    tmp_path: Path, hop: dict[str, Any]
) -> None:
    messages = _unconnected(_chain(tmp_path, "chain", hop))
    assert any(m.startswith("Port 'input' on node 'agent2'") for m in messages), messages


def _with_helper(tmp_path: Path, name: str, config: dict[str, Any]) -> Path:
    """The scaffold plus a fifth node whose IN port row no edge wires."""
    project = _scaffold(tmp_path, name)
    graph = _load(project / "orchestration.yaml")
    graph["nodes"].append(
        {
            "id": "node_helper",
            "name": "helper",
            "component_type": "AGENT",
            "script": "agent.py",
            "manifest": "agent.yaml",
            "config": config,
        }
    )
    graph["ports"].append(
        {
            "id": "port_node_helper_input",
            "node_id": "node_helper",
            "name": "input",
            "port_type": "in",
            "direction": "in",
        }
    )
    _save(project / "orchestration.yaml", graph)
    return project


def test_every_port_on_a_hidden_node_is_exempt(tmp_path: Path) -> None:
    project = _with_helper(tmp_path, "hidden", {"is_agent_tool_internal": True})
    assert _unconnected(project) == []


def test_the_same_node_not_hidden_is_reported(tmp_path: Path) -> None:
    messages = _unconnected(_with_helper(tmp_path, "visible", {}))
    assert any(m.startswith("Port 'input' on node 'helper'") for m in messages), messages


# ---------------------------------------------------------------------------
# Conflicting response contracts on a mainflow node
# ---------------------------------------------------------------------------


@pytest.fixture
def conflicting(tmp_path: Path) -> Path:
    """IN declares a narrower response contract than the OUT port the author wired."""
    project = _scaffold(tmp_path, "conflict")
    manifest = _load(project / "agent.yaml")
    manifest["input_ports"][0]["response_data"] = {
        "answer": {"type": "string", "description": "The agent's answer."}
    }
    _save(project / "agent.yaml", manifest)
    return project


def test_conflicting_contracts_refuse_the_bundle(conflicting: Path) -> None:
    with pytest.raises(BundleConfigError, match="different response_data_schema") as caught:
        orchestration_to_bundle(conflicting)
    assert CONTRACT_CONFLICT in caught.value.__notes__


def test_conflicting_contracts_fail_validation(conflicting: Path) -> None:
    report = validate_orchestration(conflicting, validate_scripts=False)
    found = [c for c in report.errors if c.id == "ORCH-BUNDLE-CONTRACT-001"]
    assert not report.valid
    assert len(found) == 1, [c.message for c in report.errors]
    assert "Component 'agent'" in found[0].message
    assert "IN port 'input'" in found[0].message


def test_agreeing_contracts_are_not_a_conflict(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, "agree")
    manifest = _load(project / "agent.yaml")
    manifest["input_ports"][0]["response_data"] = manifest["output_ports"][0]["response_data"]
    _save(project / "agent.yaml", manifest)
    report = validate_orchestration(project, validate_scripts=False)
    assert report.valid, [c.message for c in report.errors]
