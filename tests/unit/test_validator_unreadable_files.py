"""A script or ``orchestration.yaml`` export cannot read is reported, never a traceback.

Export reads each as UTF-8 and refuses the bundle on one it cannot read, so
validate refuses it too, naming the file and why, with or without script checks.
A UTF-8 file that starts with a byte-order mark is read as before.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from perceivo._bundle.capability import CAPABILITY_FILE, project_to_capability
from perceivo._bundle.component import BundleConfigError, component_to_bundle
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._cli import main
from perceivo.validator import validate_component, validate_orchestration
from perceivo.validator._component_type import ComponentTypeError, resolve_component_type
from perceivo.validator._paths import validate_component_files
from tests.unit.test_bundle_nested_orchestration import _write_leaf, _write_parent
from tests.unit.test_validator_orchestration_parity import _project
from tests.unit.test_validator_recorded_ports import (
    MANIFEST_IN_ONLY,
    SCRIPT,
    _error_ids,
    _imported,
)

LATIN1 = b"# caf\xe9\n"
BOM = b"\xef\xbb\xbf"


def _prefix(path: Path, data: bytes) -> Path:
    path.write_bytes(data + path.read_bytes())
    return path


def _not_utf8(path: Path) -> str:
    _prefix(path, LATIN1)
    return f"{path.name} is not UTF-8 text (byte 5)"


def _directory(path: Path) -> str:
    path.unlink()
    path.mkdir()
    return f"{path.name} is a directory, not a file"


UNREADABLE = [
    pytest.param(_not_utf8, id="not-utf8"),
    pytest.param(_directory, id="directory"),
]


def _script(tmp_path: Path) -> Path:
    return _imported(tmp_path, manifest=MANIFEST_IN_ONLY)


def _typeless(tmp_path: Path) -> Path:
    """A script with no manifest, so its type is read from the script itself."""
    script = tmp_path / "shield.py"
    script.write_bytes(SCRIPT.encode("utf-8"))
    return script


def _messages(report, check_id: str) -> list[str]:
    return [c.message for c in report.checks if c.id == check_id]


def test_a_component_script_that_is_not_utf8_is_refused_unjudged(tmp_path: Path) -> None:
    script = _script(tmp_path)
    reason = _not_utf8(script)
    report = validate_component(script)
    assert _error_ids(report) == ["VAL-SCRIPT-001"]
    assert _messages(report, "VAL-SCRIPT-001") == [f"The script cannot be read: {reason}"]
    with pytest.raises(BundleConfigError, match=r"shield\.py is not UTF-8 text \(byte 5\)"):
        component_to_bundle(script)


def test_validate_component_cli_reports_an_unreadable_script(tmp_path: Path) -> None:
    script = _script(tmp_path)
    _not_utf8(script)
    result = CliRunner().invoke(main, ["validate", "component", str(script), "--json"])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert "VAL-SCRIPT-001" in result.output


def test_export_cli_reports_an_unreadable_script_as_one_line(tmp_path: Path) -> None:
    script = _script(tmp_path)
    _not_utf8(script)
    result = CliRunner().invoke(main, ["export", str(script), "-o", str(tmp_path / "out.json")])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.output.startswith("Error: ")
    assert "shield.py is not UTF-8 text (byte 5)" in result.output


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda p: _prefix(p, BOM), id="bom"),
        pytest.param(lambda p: p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n")), id="crlf"),
    ],
)
def test_a_readable_component_script_is_judged_as_before(
    tmp_path: Path, change: Callable[[Path], object]
) -> None:
    control = validate_component(_script(tmp_path / "control"))
    script = _script(tmp_path / "changed")
    change(script)
    report = validate_component(script)
    assert [(c.id, c.message) for c in report.checks] == [(c.id, c.message) for c in control.checks]
    component_to_bundle(script)


def test_an_unreadable_manifest_refuses_the_export(tmp_path: Path) -> None:
    script = _script(tmp_path)
    _not_utf8(script.with_suffix(".yaml"))
    with pytest.raises(BundleConfigError, match=r"shield\.yaml is not UTF-8 text \(byte 5\)"):
        component_to_bundle(script)


def test_an_unreadable_record_refuses_the_export(tmp_path: Path) -> None:
    script = _script(tmp_path)
    script.with_name("shield.component.json").write_bytes(b"\xff\xfe{")
    with pytest.raises(BundleConfigError, match=r"shield\.component\.json cannot be read"):
        component_to_bundle(script)


@pytest.mark.parametrize("make", UNREADABLE)
@pytest.mark.parametrize("validate_scripts", [True, False])
def test_a_node_script_export_cannot_read_is_refused_in_both_modes(
    tmp_path: Path, make: Callable[[Path], str], validate_scripts: bool
) -> None:
    project = _project(tmp_path, out_hop=True)
    reason = make(project / "agent.py")
    report = validate_orchestration(str(project), validate_scripts=validate_scripts)
    assert _messages(report, "agent.py/VAL-SCRIPT-001") == [f"The script cannot be read: {reason}"]
    # No script check judges the node, and the refusal replaces the unverified warning.
    assert not [
        c.id for c in report.checks if c.id.startswith("agent.py/VAL-") and "SCRIPT" not in c.id
    ]
    assert not [c.id for c in report.checks if c.id.startswith("ORCH-BUNDLE-UNVERIFIED")]
    with pytest.raises(BundleConfigError, match=re.escape(reason)):
        orchestration_to_bundle(str(project))


@pytest.mark.parametrize("make", UNREADABLE)
@pytest.mark.parametrize("validate_scripts", [True, False])
def test_an_orchestration_yaml_export_cannot_read_fails_the_load_with_why(
    tmp_path: Path, make: Callable[[Path], str], validate_scripts: bool
) -> None:
    project = _project(tmp_path, out_hop=True)
    reason = make(project / "orchestration.yaml")
    report = validate_orchestration(str(project), validate_scripts=validate_scripts)
    assert _error_ids(report) == ["ORCH-LOAD-001"]
    assert _messages(report, "ORCH-LOAD-001") == [
        f"Could not load orchestration from {project}: {reason}."
    ]
    with pytest.raises(BundleConfigError, match=r"orchestration\.yaml"):
        orchestration_to_bundle(str(project))


def test_a_missing_orchestration_yaml_keeps_its_message(tmp_path: Path) -> None:
    report = validate_orchestration(str(tmp_path))
    assert _messages(report, "ORCH-LOAD-001") == [
        f"Could not load orchestration from {tmp_path}. Ensure orchestration.yaml exists."
    ]


@pytest.mark.parametrize("name", ["agent.py", "orchestration.yaml"])
@pytest.mark.parametrize("validate_scripts", [True, False])
def test_a_bom_prefixed_project_file_is_judged_as_before(
    tmp_path: Path, name: str, validate_scripts: bool
) -> None:
    (tmp_path / "control").mkdir()
    (tmp_path / "changed").mkdir()
    control = _project(tmp_path / "control", out_hop=True)
    project = _project(tmp_path / "changed", out_hop=True)
    _prefix(project / name, BOM)

    def ids(path: Path) -> list[str]:
        return [
            c.id
            for c in validate_orchestration(str(path), validate_scripts=validate_scripts).checks
        ]

    assert ids(project) == ids(control)
    orchestration_to_bundle(str(project))


def test_a_script_that_is_not_utf8_whose_type_is_inferred_is_refused(tmp_path: Path) -> None:
    script = _typeless(tmp_path)
    reason = _not_utf8(script)
    report = validate_component(script)
    assert [c.id for c in report.checks] == ["VAL-SCRIPT-001"]
    assert _messages(report, "VAL-SCRIPT-001") == [f"The script cannot be read: {reason}"]
    result = CliRunner().invoke(main, ["validate", "component", str(script), "--json"])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert "VAL-SCRIPT-001" in result.output


def test_inference_over_a_script_that_is_not_utf8_is_a_type_error(tmp_path: Path) -> None:
    script = _typeless(tmp_path)
    _not_utf8(script)
    with pytest.raises(ComponentTypeError, match=r"cannot read"):
        resolve_component_type(str(script), None, None)


def test_a_bom_prefixed_script_whose_type_is_inferred_is_judged_as_before(
    tmp_path: Path,
) -> None:
    (tmp_path / "control").mkdir()
    (tmp_path / "changed").mkdir()
    control = validate_component(_typeless(tmp_path / "control"))
    script = _prefix(_typeless(tmp_path / "changed"), BOM)
    report = validate_component(script)
    assert [(c.id, c.message) for c in report.checks] == [(c.id, c.message) for c in control.checks]
    assert resolve_component_type(str(script), None, None) == "TOOL"
    component_to_bundle(script)


@pytest.mark.parametrize("make", UNREADABLE)
@pytest.mark.parametrize("validate_scripts", [True, False])
def test_a_nested_orchestration_yaml_export_cannot_read_is_refused_unwarned(
    tmp_path: Path, make: Callable[[Path], str], validate_scripts: bool
) -> None:
    parent = _write_parent(tmp_path)
    reason = make(_write_leaf(parent / "nested" / "leaf", "leaf-orch") / "orchestration.yaml")
    report = validate_orchestration(str(parent), validate_scripts=validate_scripts)
    assert [c.id for c in report.errors if c.id.endswith("ORCH-LOAD-001")] == [
        "Child/ORCH-LOAD-001"
    ]
    assert reason in _messages(report, "Child/ORCH-LOAD-001")[0]
    assert not [c.id for c in report.checks if c.id.startswith("ORCH-BUNDLE-UNVERIFIED")]
    with pytest.raises(BundleConfigError, match=re.escape(reason)):
        orchestration_to_bundle(str(parent))


def test_a_capability_file_that_is_not_utf8_refuses_the_export(tmp_path: Path) -> None:
    (tmp_path / CAPABILITY_FILE).write_bytes(LATIN1)
    with pytest.raises(BundleConfigError, match=r"capability\.yaml is not UTF-8 text \(byte 5\)"):
        project_to_capability(tmp_path)


def test_a_component_script_path_that_is_a_directory_is_refused(tmp_path: Path) -> None:
    script = _script(tmp_path)
    reason = _directory(script)
    report = validate_component_files(script)
    assert [c.id for c in report.checks] == ["VAL-SCRIPT-001"]
    assert _messages(report, "VAL-SCRIPT-001") == [f"The script cannot be read: {reason}"]
    result = CliRunner().invoke(main, ["validate", "component", str(script), "--json"])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert "VAL-SCRIPT-001" in result.output


@pytest.mark.parametrize("validate_scripts", [True, False])
def test_a_missing_nested_orchestration_yaml_keeps_the_unverified_warning(
    tmp_path: Path, validate_scripts: bool
) -> None:
    parent = _write_parent(tmp_path)
    (_write_leaf(parent / "nested" / "leaf", "leaf-orch") / "orchestration.yaml").unlink()
    report = validate_orchestration(str(parent), validate_scripts=validate_scripts)
    assert [c.id for c in report.errors if c.id.endswith("ORCH-LOAD-001")] == [
        "Child/ORCH-LOAD-001"
    ]
    assert [c.id for c in report.checks if c.id.startswith("ORCH-BUNDLE-UNVERIFIED")]
