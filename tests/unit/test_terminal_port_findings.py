"""START and END port findings, from validate and from the bundler.

A START or END whose ports break the creation rule is one finding whichever
path reports it, and the refusal lists every port the component bundles with
its type, direction and the file it comes from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle._port_consistency import TerminalPortShapeError, reject_terminal_port_shape
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._bundle.schema import ComponentExportData, PortExportData
from perceivo._cli import main as perceivo_cli
from perceivo.validator import validate_orchestration
from perceivo.validator._orchestration._loader import _load_from_directory, _node_port_rows
from perceivo.validator._port_rules import validate_port_rules

# ---------------------------------------------------------------------------
# One defect, one finding, from validate and from the bundler
# ---------------------------------------------------------------------------

_START_TOTAL = "START must have exactly 2 ports (EXTERNAL_IN and OUT), found 3"


def _scaffold(tmp_path: Path, name: str) -> Path:
    result = CliRunner().invoke(
        perceivo_cli, ["new", "orchestration", name, "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    return tmp_path / name


def _edit_graph(project: Path, edit) -> None:
    path = project / "orchestration.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def upper_trigger(tmp_path: Path) -> Path:
    """A START node adding a ``trigger`` port typed ``EXTERNAL_IN`` beside the manifest's."""
    project = _scaffold(tmp_path, "upper")
    _edit_graph(
        project,
        lambda data: data["ports"].append(
            {
                "id": "p_trigger",
                "node_id": "node_start",
                "name": "trigger",
                "port_type": "EXTERNAL_IN",
            }
        ),
    )
    return project


@pytest.mark.parametrize("scripts", [False, True], ids=["files", "scripts"])
def test_an_upper_case_external_port_is_one_finding(upper_trigger: Path, scripts: bool) -> None:
    report = validate_orchestration(upper_trigger, validate_scripts=scripts)
    refused = [c.message for c in report.errors if _START_TOTAL in c.message]
    assert not report.valid
    assert len(refused) == 1, [c.message for c in report.errors]


def test_an_upper_case_external_port_is_refused_by_the_bundler(upper_trigger: Path) -> None:
    with pytest.raises(TerminalPortShapeError) as caught:
        orchestration_to_bundle(upper_trigger)
    assert caught.value.finding == _START_TOTAL
    message = str(caught.value)
    assert message.startswith(f"Component 'start': {_START_TOTAL}. Its ports: ")
    assert "'trigger' (type 'EXTERNAL_IN', direction IN, from orchestration.yaml)" in message


# ---------------------------------------------------------------------------
# The bundled ports, not the rows
# ---------------------------------------------------------------------------


def _drop_start_external_row(data: dict[str, Any]) -> None:
    data["ports"] = [
        p
        for p in data["ports"]
        if not (p["node_id"] == "node_start" and p["port_type"] == "external_in")
    ]
    data["edges"] = [e for e in data["edges"] if "external" not in str(e.get("source_port_id", ""))]


def test_a_manifest_only_external_port_is_accepted(tmp_path: Path) -> None:
    """The platform adds a node port for every component port the rows omit, so a
    START whose external port only its manifest declares is created whole."""
    project = _scaffold(tmp_path, "manifest_only")
    _edit_graph(project, _drop_start_external_row)
    rows = _node_port_rows(
        yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))["ports"],
        "node_start",
    )
    assert [c.message for c in validate_port_rules("START", rows)] == [
        "START must have exactly 1 EXTERNAL_IN port (found 0)"
    ]

    loaded = _load_from_directory(str(project))
    assert loaded is not None
    start = next(c for c in loaded["components"] if c["component_type"] == "START")
    assert sorted(p["port_type"] for p in start["ports"]) == ["external_in", "out"]
    report = validate_orchestration(project, validate_scripts=True)
    assert not [c.message for c in report.errors if "START must" in c.message]
    assert report.valid, [c.message for c in report.errors]


def test_rows_judge_a_component_that_cannot_be_built(
    upper_trigger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "perceivo.validator._orchestration._component_ports._bundled_component",
        lambda *_args: None,
    )
    report = validate_orchestration(upper_trigger, validate_scripts=True)
    refused = [c.id for c in report.errors if _START_TOTAL in c.message]
    assert refused == ["start/PORT-RULE-001"], [c.message for c in report.errors]


# ---------------------------------------------------------------------------
# The bundler adds every port as bundled to the platform's message, and no advice
# ---------------------------------------------------------------------------

_EXT, _OUT = ("external_input", "external_in", "IN"), ("output", "out", "OUT")


def _start(*ports: tuple[str, str, str]) -> dict[str, ComponentExportData]:
    exported = [PortExportData(key=k, port_type=t, direction=d) for k, t, d in ports]
    return {"c": ComponentExportData(name="start", category="START", ports=exported)}


def _ext_found(n: int) -> str:
    return f"START must have exactly 1 EXTERNAL_IN port (found {n})"


_OUT_FOUND_0 = "START must have exactly 1 OUT port (found 0)"


@pytest.mark.parametrize(
    ("ports", "pinned", "manifest", "finding", "listed"),
    [
        (
            (_EXT, ("trigger", "external_in", "IN"), _OUT),
            True,
            None,
            _ext_found(2),
            "'external_input' (type 'external_in', direction IN, from orchestration.yaml), "
            "'trigger' (type 'external_in', direction IN, from orchestration.yaml), "
            "'output' (type 'out', direction OUT, from orchestration.yaml)",
        ),
        (
            (_EXT, ("trigger", "external_in", "IN"), _OUT),
            False,
            (0, 2),
            _ext_found(2),
            "'external_input' (type 'external_in', direction IN, from the manifest), "
            "'trigger' (type 'external_in', direction IN, from orchestration.yaml), "
            "'output' (type 'out', direction OUT, from the manifest)",
        ),
        (
            (_OUT,),
            False,
            (0,),
            _ext_found(0),
            "'output' (type 'out', direction OUT, from the manifest)",
        ),
        (
            (_EXT, _OUT, ("extra", "in", "IN")),
            True,
            None,
            _START_TOTAL,
            "'external_input' (type 'external_in', direction IN, from orchestration.yaml), "
            "'output' (type 'out', direction OUT, from orchestration.yaml), "
            "'extra' (type 'in', direction IN, from orchestration.yaml)",
        ),
        (
            (_EXT, _OUT, ("extra", "EXTERNAL_IN", "IN")),
            False,
            (0, 1, 2),
            _START_TOTAL,
            "'external_input' (type 'external_in', direction IN, from the manifest), "
            "'output' (type 'out', direction OUT, from the manifest), "
            "'extra' (type 'EXTERNAL_IN', direction IN, from the manifest)",
        ),
        (
            (_EXT, ("output", "OUT", "OUT"), ("o2", "Out", "OUT")),
            False,
            (0, 1, 2),
            _OUT_FOUND_0,
            "'external_input' (type 'external_in', direction IN, from the manifest), "
            "'output' (type 'OUT', direction OUT, from the manifest), "
            "'o2' (type 'Out', direction OUT, from the manifest)",
        ),
        (
            (_EXT, ("output", "OUT", "OUT"), ("o2", "Out", "OUT")),
            True,
            None,
            _OUT_FOUND_0,
            "'external_input' (type 'external_in', direction IN, from orchestration.yaml), "
            "'output' (type 'OUT', direction OUT, from orchestration.yaml), "
            "'o2' (type 'Out', direction OUT, from orchestration.yaml)",
        ),
        (
            (_EXT, _OUT, ("external_input", "external_in", "OUT")),
            False,
            (0, 1),
            _ext_found(2),
            "'external_input' (type 'external_in', direction IN, from the manifest), "
            "'output' (type 'out', direction OUT, from the manifest), "
            "'external_input' (type 'external_in', direction OUT, from orchestration.yaml)",
        ),
        (
            (("ext2", "external_in", "IN"), _EXT, _OUT, ("trigger", "external_in", "IN")),
            False,
            (0, 1, 2),
            _ext_found(3),
            "'ext2' (type 'external_in', direction IN, from the manifest), "
            "'external_input' (type 'external_in', direction IN, from the manifest), "
            "'output' (type 'out', direction OUT, from the manifest), "
            "'trigger' (type 'external_in', direction IN, from orchestration.yaml)",
        ),
        (
            (_OUT,),
            False,
            None,
            _ext_found(0),
            "'output' (type 'out', direction OUT, from the manifest)",
        ),
        (
            (),
            False,
            None,
            _ext_found(0),
            "none",
        ),
    ],
    ids=[
        "pinned",
        "renamed",
        "missing",
        "pinned-total",
        "manifest-extra",
        "case-variants",
        "pinned-case-variants",
        "direction",
        "renamed-beside-surplus",
        "unmerged",
        "no-ports",
    ],
)
def test_the_message_lists_every_port_as_bundled(ports, pinned, manifest, finding, listed) -> None:
    components = _start(*ports)
    exported = components["c"].ports
    manifest_ports = {} if manifest is None else {"c": [exported[i] for i in manifest]}
    with pytest.raises(TerminalPortShapeError) as caught:
        reject_terminal_port_shape(components, {"c"} if pinned else set(), manifest_ports)
    assert caught.value.finding == finding
    assert str(caught.value) == f"Component 'start': {finding}. Its ports: {listed}."


def test_a_well_formed_terminal_is_not_refused() -> None:
    components = _start(_EXT, _OUT)
    reject_terminal_port_shape(components, {"c"}, {"c": components["c"].ports})


def _edit_start(project: Path, edit) -> None:
    path = project / "start.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _pin_upper_external(data: dict[str, Any]) -> None:
    node = next(n for n in data["nodes"] if n["id"] == "node_start")
    node["component_ports"] = [
        {"key": "external_input", "direction": "IN", "port_type": "EXTERNAL_IN"},
        {"key": "output", "direction": "OUT", "port_type": "out"},
    ]


def _upper_manifest_out(project: Path) -> None:
    _edit_start(project, lambda d: d["output_ports"][0].update(port_type="OUT"))


def _pinned_upper(project: Path) -> None:
    _edit_graph(project, _pin_upper_external)


def _manifest_second_external(project: Path) -> None:
    _edit_start(
        project,
        lambda d: d.update(input_ports=[{"port_name": "ext2", "port_type": "external_in"}]),
    )


@pytest.mark.parametrize(
    ("edit", "finding", "listed"),
    [
        (
            _upper_manifest_out,
            _OUT_FOUND_0,
            [
                "'external_input' (type 'external_in', direction IN, from the manifest)",
                "'output' (type 'OUT', direction OUT, from the manifest)",
            ],
        ),
        (
            _pinned_upper,
            _ext_found(0),
            [
                "'external_input' (type 'EXTERNAL_IN', direction IN, from orchestration.yaml)",
                "'output' (type 'out', direction OUT, from orchestration.yaml)",
            ],
        ),
        (
            _manifest_second_external,
            _ext_found(2),
            [
                "'ext2' (type 'external_in', direction IN, from the manifest)",
                "'external_input' (type 'external_in', direction IN, from the manifest)",
            ],
        ),
    ],
    ids=["upper-manifest-out", "pinned-upper", "manifest-two-external"],
)
def test_the_message_lists_the_ports_a_project_bundles(
    tmp_path: Path, edit, finding: str, listed: list[str]
) -> None:
    project = _scaffold(tmp_path, "cause")
    edit(project)
    with pytest.raises(TerminalPortShapeError) as caught:
        orchestration_to_bundle(project)
    message = str(caught.value)
    assert message.startswith(f"Component 'start': {finding}. Its ports: ")
    for item in listed:
        assert item in message, item
