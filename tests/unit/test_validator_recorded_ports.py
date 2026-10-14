"""An imported component's port rules are judged on the rows it will be created with.

The platform validates a component's ports rows, not its manifest. A seed whose
manifest declares an ``out`` port its rows lack is created clean there, so it
must validate clean here while the manifest is the one the import wrote; once
the manifest is edited, the manifest owns the ports again.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from perceivo._bundle.component import bundle_to_component
from perceivo.validator import validate_component

SCRIPT = """
import perceivo
from perceivo.primitives import ToolInput, ToolOutput

@perceivo.tool
async def shield(input: ToolInput) -> ToolOutput:
    return ToolOutput(response_data={})
"""

MANIFEST = """name: Shield
extension_type: tool
input_ports:
- port_name: input
  port_type: in
output_ports:
- port_name: output
  port_type: out
"""

# Valid on its own ports, so a refusal can only come from the record.
MANIFEST_IN_ONLY = """name: Shield
extension_type: tool
input_ports:
- port_name: input
  port_type: in
"""

ROW = {"key": "input", "port_type": "in", "direction": "IN"}

ROOT = Path(__file__).resolve().parents[2]


def _imported(tmp_path: Path, ports: list | None = None, manifest: str = MANIFEST) -> Path:
    if ports is None:
        ports = [{"key": "input", "port_type": "in", "direction": "IN"}]
    bundle = {
        "export_type": "component",
        "exported_at": "2026-01-01T00:00:00Z",
        "component": {
            "name": "shield",
            "category": "TOOL",
            "script": SCRIPT,
            "manifest_yaml": manifest,
            "ports": ports,
        },
    }
    return bundle_to_component(bundle, tmp_path / "work") / "shield.py"


def _port_errors(report) -> list[str]:
    return [c.message for c in report.checks if c.severity == "error"]


def test_an_imported_tool_is_judged_on_its_recorded_rows(tmp_path: Path) -> None:
    report = validate_component(_imported(tmp_path))
    assert report.valid, _port_errors(report)


def test_an_edited_manifest_owns_the_ports_again(tmp_path: Path) -> None:
    script = _imported(tmp_path)
    manifest = script.with_suffix(".yaml")
    manifest.write_bytes(manifest.read_bytes() + b"description: edited\n")
    report = validate_component(script)
    assert not report.valid
    assert any("OUT ports" in message for message in _port_errors(report))


def test_a_record_of_another_format_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    assert validate_component(script).valid
    record_path = script.with_name("shield.component.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["format"] = "something_else"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_a_record_that_is_not_json_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    script.with_name("shield.component.json").write_text("{not json", encoding="utf-8")
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_a_record_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    script.with_name("shield.component.json").write_bytes(b"\xff\xfe{")
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_an_unreadable_record_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    read_text = Path.read_text

    def denied(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "shield.component.json":
            raise OSError(errno.EACCES, os.strerror(errno.EACCES), str(self))
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    report = validate_component(script)
    assert _error_ids(report) == ["VAL-RECORD-001"]
    assert any("shield.component.json cannot be read" in c.message for c in report.checks)


def test_a_directory_named_as_the_record_is_no_record(tmp_path: Path) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    record = script.with_name("shield.component.json")
    record.unlink()
    record.mkdir()
    assert validate_component(script).valid


def _cli_validate(script: Path, *, pass_manifest: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    command = "from perceivo._cli import main;import sys;sys.argv=['perceivo']+sys.argv[1:];main()"
    manifest = [str(script.with_suffix(".yaml"))] if pass_manifest else []
    args = ["validate", "component", str(script), *manifest, "--json"]
    return subprocess.run(
        [sys.executable, "-c", command, *args], capture_output=True, text=True, env=env
    )


def test_the_cli_reads_the_recorded_rows_too(tmp_path: Path) -> None:
    completed = _cli_validate(_imported(tmp_path))
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_cli_reads_a_crlf_manifest_as_the_exporter_does(tmp_path: Path) -> None:
    script = _imported(tmp_path)
    manifest = script.with_suffix(".yaml")
    manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
    completed = _cli_validate(script)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "OUT ports" in completed.stdout


def test_a_crlf_manifest_is_judged_as_the_exporter_reads_it(tmp_path: Path) -> None:
    # The exporter hashes the manifest's bytes, so a CRLF checkout of it reads as
    # edited there; the validator must not use rows the exporter would drop.
    script = _imported(tmp_path)
    manifest = script.with_suffix(".yaml")
    manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
    report = validate_component(script)
    assert not report.valid
    assert any("OUT ports" in message for message in _port_errors(report))


def test_a_record_with_no_port_rows_stands(tmp_path: Path) -> None:
    script = _imported(tmp_path, ports=[])
    record = json.loads(script.with_name("shield.component.json").read_text(encoding="utf-8"))
    assert record["component"]["ports"] == []
    report = validate_component(script)
    # Judged on no rows at all, not on the manifest's input and output ports.
    assert not any("OUT ports" in message for message in _port_errors(report))
    assert any("IN port" in message for message in _port_errors(report))


def _with_rows(script: Path, rows: list) -> None:
    record_path = script.with_name("shield.component.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["component"]["ports"] = rows
    record_path.write_text(json.dumps(record), encoding="utf-8")


def test_a_recorded_row_that_is_not_a_port_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path)
    _with_rows(script, ["x"])
    report = validate_component(script)
    assert [c.id for c in report.checks if c.severity == "error"] == ["VAL-RECORD-001"]


def test_a_recorded_row_the_exporter_rejects_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path)
    _with_rows(script, [{"key": "input", "port_type": "in"}])
    report = validate_component(script)
    assert [c.id for c in report.checks if c.severity == "error"] == ["VAL-RECORD-001"]


def _with_component(script: Path, component: object) -> None:
    record_path = script.with_name("shield.component.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["component"] = component
    record_path.write_text(json.dumps(record), encoding="utf-8")


def _error_ids(report) -> list[str]:
    return [c.id for c in report.checks if c.severity == "error"]


@pytest.mark.parametrize("component", [[ROW], [], ["x"], "x", None])
def test_a_record_whose_component_is_not_a_mapping_is_refused(
    tmp_path: Path, component: object
) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    _with_component(script, component)
    report = validate_component(script)
    assert _error_ids(report) == ["VAL-RECORD-001"]
    assert "'component' must be a mapping" in _port_errors(report)[0]


@pytest.mark.parametrize("component", [[ROW], None])
def test_a_bad_component_is_refused_behind_an_edited_manifest(
    tmp_path: Path, component: object
) -> None:
    # Export checks the record's shape before it compares the manifest digest.
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    manifest = script.with_suffix(".yaml")
    manifest.write_bytes(manifest.read_bytes() + b"description: edited\n")
    assert validate_component(script).valid
    _with_component(script, component)
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_an_undeclared_component_field_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    _with_component(script, {"ports": [ROW], "colour": "red"})
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_a_component_field_export_rejects_is_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    _with_component(script, {"ports": [ROW], "tags": 5})
    report = validate_component(script)
    assert _error_ids(report) == ["VAL-RECORD-001"]
    assert "tags" in _port_errors(report)[0]


def test_null_recorded_ports_are_refused_not_replaced(tmp_path: Path) -> None:
    script = _imported(tmp_path)
    _with_rows(script, None)
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_recorded_ports_that_are_not_a_list_are_refused(tmp_path: Path) -> None:
    script = _imported(tmp_path)
    _with_rows(script, {"key": "input"})
    assert _error_ids(validate_component(script)) == ["VAL-RECORD-001"]


def test_a_record_without_a_ports_key_leaves_the_manifest_ports(tmp_path: Path) -> None:
    # The exporter keeps the manifest's ports when the record has none to overlay.
    script = _imported(tmp_path)
    record_path = script.with_name("shield.component.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    del record["component"]["ports"]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    report = validate_component(script)
    assert "VAL-RECORD-001" not in _error_ids(report)
    assert any("OUT ports" in message for message in _port_errors(report))


def _with_bad_record(script: Path, shape: str) -> None:
    if shape == "not json":
        script.with_name("shield.component.json").write_text("{not json", encoding="utf-8")
    elif shape == "null ports":
        # Refused only against the manifest the record was written with, so
        # the verdict depends on which manifest is read.
        _with_rows(script, None)
    else:
        _with_component(script, {"ports": [ROW], "colour": "red"})


@pytest.mark.parametrize("shape", ["not json", "undeclared field"])
def test_a_bad_record_is_refused_with_no_manifest(tmp_path: Path, shape: str) -> None:
    # Export reads the record against an empty manifest when there is none.
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    script.with_suffix(".yaml").unlink()
    _with_bad_record(script, shape)
    assert "VAL-RECORD-001" in _error_ids(validate_component(script))
    completed = _cli_validate(script, pass_manifest=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "VAL-RECORD-001" in completed.stdout


@pytest.mark.parametrize("shape", ["not json", "undeclared field", "null ports"])
def test_the_cli_reads_the_manifest_export_reads_when_none_is_named(
    tmp_path: Path, shape: str
) -> None:
    script = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    assert _cli_validate(script, pass_manifest=False).returncode == 0
    _with_bad_record(script, shape)
    completed = _cli_validate(script, pass_manifest=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "VAL-RECORD-001" in completed.stdout


def test_the_cli_reads_the_manifest_beside_a_linked_scripts_target(tmp_path: Path) -> None:
    # Export reads <script>.yaml of the resolved script, not of the link.
    target = _imported(tmp_path, manifest=MANIFEST_IN_ONLY)
    _with_rows(target, None)
    link = tmp_path / "linked" / "shield.py"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    # A valid manifest the record was not written with, so its ports stand.
    link.with_suffix(".yaml").write_text(
        MANIFEST_IN_ONLY + "description: decoy\n", encoding="utf-8"
    )
    completed = _cli_validate(link, pass_manifest=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "VAL-RECORD-001" in completed.stdout
