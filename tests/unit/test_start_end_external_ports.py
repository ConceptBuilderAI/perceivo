"""START and END carry exactly one external port each, as ``publish`` bundles them.

The bundler merges each ``orchestration.yaml`` node's ports into its component's
manifest ports by ``(key, direction)``, so a node that names its external port
differently from the manifest gives the component a second one. The platform
refuses that START with "START must have exactly 1 EXTERNAL_IN port (found 2)",
and END likewise. The bundler and ``perceivo validate orchestration`` refuse it
with the same message before upload, and the scaffolds name both sides alike.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle._port_consistency import TerminalPortShapeError
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._cli import main as perceivo_cli
from perceivo.orchestration import publish
from perceivo.orchestration._builder._prebuilt import _write_prebuilt
from perceivo.validator import validate_orchestration
from perceivo.validator._orchestration._loader import _load_from_directory
from perceivo.validator._port_rules import validate_port_rules

#: The platform's terminal-component rule: one external port and one flow port.
_EXPECTED = {
    "START": Counter({"external_in": 1, "out": 1}),
    "END": Counter({"external_out": 1, "in": 1}),
}


def _scaffold(tmp_path: Path, name: str = "proj") -> Path:
    result = CliRunner().invoke(
        perceivo_cli, ["new", "orchestration", name, "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    return tmp_path / name


def _terminal_components(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = {c["category"]: c for c in bundle["components"].values() if c["category"] in _EXPECTED}
    assert sorted(found) == ["END", "START"]
    return found


def _assert_terminal_cardinality(bundle: dict[str, Any]) -> None:
    for category, component in _terminal_components(bundle).items():
        ports = component["ports"]
        assert Counter(p["port_type"] for p in ports) == _EXPECTED[category], ports
        assert validate_port_rules(category, ports) == []


def _external_names(project: Path) -> dict[str, str]:
    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    return {
        p["port_type"]: p["name"]
        for p in data["ports"]
        if p["port_type"] in ("external_in", "external_out")
    }


def _rename_node_ports(project: Path, renames: dict[str, str]) -> None:
    path = project / "orchestration.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    hits = 0
    for port in data["ports"]:
        if port.get("name") in renames:
            port["name"] = renames[port["name"]]
            hits += 1
    assert hits == len(renames), "the fixture rewrite matched nothing"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# The scaffold
# ---------------------------------------------------------------------------


def test_scaffold_node_ports_name_the_manifest_external_ports(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    start = yaml.safe_load((project / "start.yaml").read_text(encoding="utf-8"))
    end = yaml.safe_load((project / "end.yaml").read_text(encoding="utf-8"))
    assert _external_names(project) == {
        "external_in": start["external_in_port"]["port_name"],
        "external_out": end["external_out_port"]["port_name"],
    }


async def test_scaffold_publish_dry_run_bundles_one_external_port_each(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    result = await publish(str(project), name="scaffold", dry_run=True)
    _assert_terminal_cardinality(result.bundle)
    keys = {(p["node_ref"], p["component_port_key"]) for p in result.bundle["ports"]}
    assert ("node_start", "external_input") in keys
    assert ("node_end", "external_output") in keys


def test_scaffold_validates_including_bundled_ports(tmp_path: Path) -> None:
    report = validate_orchestration(_scaffold(tmp_path))
    assert report.valid, [c.message for c in report.errors]


# ---------------------------------------------------------------------------
# A node that names its external port differently from the manifest
# ---------------------------------------------------------------------------

#: The platform's messages for a START or END carrying a second external port.
_START_REFUSAL = "START must have exactly 1 EXTERNAL_IN port (found 2)"
_END_REFUSAL = "END must have exactly 1 EXTERNAL_OUT port (found 2)"


@pytest.fixture
def renamed_start(tmp_path: Path) -> Path:
    """The scaffold with its START node port named ``trigger``, its manifest's ``external_input``."""
    project = _scaffold(tmp_path, "renamed")
    _rename_node_ports(project, {"external_input": "trigger"})
    assert _external_names(project)["external_in"] == "trigger"
    return project


def test_a_renamed_start_port_is_refused_by_the_bundler(renamed_start: Path) -> None:
    before = (renamed_start / "orchestration.yaml").read_bytes()
    with pytest.raises(TerminalPortShapeError) as caught:
        orchestration_to_bundle(renamed_start)
    assert caught.value.finding == _START_REFUSAL
    assert str(caught.value).startswith(f"Component 'start': {_START_REFUSAL}. ")
    assert (renamed_start / "orchestration.yaml").read_bytes() == before


async def test_a_renamed_start_port_is_refused_by_publish(renamed_start: Path) -> None:
    with pytest.raises(TerminalPortShapeError, match=re.escape(_START_REFUSAL)):
        await publish(str(renamed_start), name="renamed", dry_run=True)


def test_a_renamed_start_port_fails_validation(renamed_start: Path) -> None:
    report = validate_orchestration(renamed_start, validate_scripts=False)
    refused = [c for c in report.errors if _START_REFUSAL in c.message]
    assert not report.valid
    assert [c.id for c in refused] == ["ORCH-BUNDLE-PORTS-001"], [c.message for c in report.errors]


def test_a_renamed_end_port_is_refused_by_the_bundler(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, "renamed_end")
    _rename_node_ports(project, {"external_output": "result"})
    with pytest.raises(TerminalPortShapeError) as caught:
        orchestration_to_bundle(project)
    assert caught.value.finding == _END_REFUSAL
    report = validate_orchestration(project, validate_scripts=False)
    assert [c.id for c in report.errors if _END_REFUSAL in c.message] == ["ORCH-BUNDLE-PORTS-001"]


#: A pinned START listing a second external port beside the manifest's.
_PINNED_TWO_EXTERNAL = [
    {"key": "external_input", "direction": "IN", "port_type": "external_in"},
    {"key": "trigger", "direction": "IN", "port_type": "external_in"},
    {"key": "output", "direction": "OUT", "port_type": "out"},
]


@pytest.mark.parametrize("pinned", [False, True], ids=["renamed", "pinned"])
def test_validation_judges_the_external_row_as_written(tmp_path: Path, pinned: bool) -> None:
    """A START with two external ports is refused, its row judged under its own name.

    The manifest's external port forwards its request. A renamed row is a second
    port rather than that one, and a pinned list states no forwarding, so in
    neither case does the row forward what the manifest's port does.
    """
    project = _scaffold(tmp_path, "two_external")
    start_path = project / "start.yaml"
    start = yaml.safe_load(start_path.read_text(encoding="utf-8"))
    start["external_in_port"]["response_data_passthrough"] = "request_data"
    start_path.write_text(yaml.safe_dump(start, sort_keys=False), encoding="utf-8")
    if pinned:
        path = project / "orchestration.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        node = next(n for n in data["nodes"] if n["id"] == "node_start")
        node["component_ports"] = _PINNED_TWO_EXTERNAL
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        _rename_node_ports(project, {"external_input": "trigger"})

    report = validate_orchestration(project, validate_scripts=False)
    refused = [c for c in report.errors if c.id == "ORCH-BUNDLE-PORTS-001"]
    assert [c.message.split(". ")[0] for c in refused] == [
        f"Component 'start': {_START_REFUSAL}"
    ], [c.message for c in report.errors]
    loaded = _load_from_directory(str(project))
    assert loaded is not None
    rows = [p for p in loaded["ports"] if p["port_type"] == "external_in"]
    assert [r["name"] for r in rows] == ["external_input" if pinned else "trigger"]
    assert "response_data_passthrough" not in rows[0], rows[0]


# ---------------------------------------------------------------------------
# A genuine duplicate is reported locally (control: this must fail)
# ---------------------------------------------------------------------------


@pytest.fixture
def duplicate_project(tmp_path: Path) -> Path:
    """A START node declaring a second external_in port beside the manifest's."""
    project = _scaffold(tmp_path, "dup")
    path = project / "orchestration.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["ports"].append(
        {
            "id": "port_node_start_second",
            "node_id": "node_start",
            "name": "second_external",
            "port_type": "external_in",
        }
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return project


def test_duplicate_external_port_is_refused_by_the_bundler(duplicate_project: Path) -> None:
    with pytest.raises(TerminalPortShapeError) as caught:
        orchestration_to_bundle(duplicate_project)
    assert caught.value.finding == _START_REFUSAL


def test_duplicate_external_port_fails_validation_on_the_bundle(duplicate_project: Path) -> None:
    report = validate_orchestration(duplicate_project, validate_scripts=False)
    bundled = [c for c in report.errors if c.id.startswith("ORCH-BUNDLE-PORTS-")]
    assert not report.valid
    assert len(bundled) == 1, [c.message for c in report.errors]
    assert bundled[0].message.startswith(f"Component 'start': {_START_REFUSAL}. ")


def test_bundled_finding_already_reported_is_not_repeated(duplicate_project: Path) -> None:
    report = validate_orchestration(duplicate_project, validate_scripts=True)
    messages = [c.message for c in report.errors if "EXTERNAL_IN" in c.message]
    assert not report.valid
    assert len(messages) == 1, messages


def test_unbundleable_project_is_a_warning_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _scaffold(tmp_path)

    def _refuse(_project_dir: Any) -> dict[str, Any]:
        raise ValueError("cannot bundle")

    monkeypatch.setattr("perceivo._bundle.orchestration.orchestration_to_bundle", _refuse)
    report = validate_orchestration(project, validate_scripts=False)
    unverified = [c for c in report.checks if c.id == "ORCH-BUNDLE-UNVERIFIED-001"]
    assert report.valid
    assert len(unverified) == 1
    assert unverified[0].severity == "warning"
    assert "cannot bundle" in unverified[0].message


# ---------------------------------------------------------------------------
# The prebuilt writer scaffolds no terminal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ctype", ["START", "END"])
def test_prebuilt_refuses_a_terminal(ctype: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=f"^{ctype} is not a prebuilt component type$"):
        _write_prebuilt(tmp_path, "node", "echo", ctype)
    assert list(tmp_path.iterdir()) == []
