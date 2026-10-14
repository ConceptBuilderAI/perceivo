"""A node whose component record export refuses fails orchestration validate.

``publish`` builds each node's component with the exporter, which overlays the
record beside the script (``<stem>.component.json``). A record it refuses stops
the whole bundle, so the project must fail validate with the record's error,
not pass with a warning that its bundled ports went unchecked.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from pydantic import ValidationError

from perceivo._bundle._component_sidecar import read_manifest, write_sidecar
from perceivo._bundle.component import BundleConfigError, component_to_bundle
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._bundle.schema import ComponentExportData
from perceivo.validator._orchestration import validate_orchestration
from perceivo.validator._orchestration._record_checks import component_record_checks
from tests.unit.test_validator_orchestration_parity import _project


def _recorded(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    *,
    crlf: bool = False,
    named: bool = True,
) -> Path:
    """START -> AGENT -> END, the AGENT carrying its record with *mutate* applied.

    *crlf* writes the AGENT's manifest with CRLF line ends before the record is
    taken over it; *named* False drops the node's ``manifest``, so the one
    beside the script is the manifest export reads.
    """
    project = _project(tmp_path, out_hop=True)
    script, manifest = project / "agent.py", project / "agent.yaml"
    if crlf:
        manifest.write_bytes(manifest.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    data = component_to_bundle(script, manifest)["component"]
    path = write_sidecar(script, ComponentExportData.model_validate(data), read_manifest(manifest))
    record = json.loads(path.read_text(encoding="utf-8"))
    mutate(record)
    path.write_text(json.dumps(record), encoding="utf-8")
    if not named:
        graph_file = project / "orchestration.yaml"
        graph = yaml.safe_load(graph_file.read_text(encoding="utf-8"))
        next(n for n in graph["nodes"] if n["id"] == "agent").pop("manifest")
        graph_file.write_text(yaml.safe_dump(graph), encoding="utf-8")
    return project


def _field(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    return lambda record: record["component"].__setitem__(key, value)


def _record_errors(project: Path, *, validate_scripts: bool = False) -> list[str]:
    report = validate_orchestration(str(project), validate_scripts=validate_scripts)
    return [c.id for c in report.errors if c.id.endswith("/VAL-RECORD-001")]


def test_an_unchanged_record_validates(tmp_path: Path) -> None:
    project = _recorded(tmp_path, lambda record: None)
    report = validate_orchestration(str(project), validate_scripts=False)
    assert report.valid, [c.message for c in report.errors]
    orchestration_to_bundle(str(project))


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda record: record.__setitem__("format", "other"), id="format"),
        pytest.param(lambda record: record.__setitem__("component", [1]), id="component-list"),
        pytest.param(_field("surprise", 1), id="unknown-key"),
        pytest.param(_field("ports", "x"), id="ports-str"),
        pytest.param(_field("ports", [5]), id="ports-row"),
        pytest.param(_field("tags", 5), id="tags"),
        pytest.param(_field("requires_gpu", "maybe"), id="requires-gpu"),
    ],
)
def test_a_record_export_refuses_fails_validate(tmp_path: Path, mutate: Callable) -> None:
    project = _recorded(tmp_path, mutate)
    with pytest.raises((BundleConfigError, ValidationError)):
        orchestration_to_bundle(str(project))
    assert len(_record_errors(project)) == 1


def test_a_record_that_is_not_json_fails_with_script_checks_too(tmp_path: Path) -> None:
    project = _recorded(tmp_path, lambda record: None)
    (project / "agent.component.json").write_text("{not json", encoding="utf-8")
    assert len(_record_errors(project)) == 1
    assert len(_record_errors(project, validate_scripts=True)) == 1


def test_the_record_error_replaces_the_unverified_ports_warning(tmp_path: Path) -> None:
    project = _recorded(tmp_path, _field("tags", 5))
    ids = [c.id for c in validate_orchestration(str(project), validate_scripts=False).checks]
    assert "ORCH-BUNDLE-UNVERIFIED-001" not in ids
    assert len(_record_errors(project)) == 1


def test_a_record_that_is_not_utf8_is_reported_not_raised(tmp_path: Path) -> None:
    project = _recorded(tmp_path, lambda record: None)
    (project / "agent.component.json").write_bytes(b"\xff\xfe{")
    with pytest.raises(BundleConfigError, match=r"agent\.component\.json cannot be read"):
        orchestration_to_bundle(str(project))
    assert len(_record_errors(project)) == 1
    assert len(_record_errors(project, validate_scripts=True)) == 1


def test_an_unreadable_record_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _recorded(tmp_path, lambda record: None)
    read_text = Path.read_text

    def denied(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "agent.component.json":
            raise OSError(errno.EACCES, os.strerror(errno.EACCES), str(self))
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    report = validate_orchestration(str(project), validate_scripts=False)
    errors = [c.message for c in report.errors if c.id.endswith("/VAL-RECORD-001")]
    assert len(errors) == 1
    assert "agent.component.json cannot be read" in errors[0]


def test_a_directory_named_as_the_record_is_no_record(tmp_path: Path) -> None:
    # Export reads only a file there, so a directory is no record to either.
    project = _recorded(tmp_path, lambda record: None)
    (project / "agent.component.json").unlink()
    (project / "agent.component.json").mkdir()
    orchestration_to_bundle(str(project))
    assert validate_orchestration(str(project), validate_scripts=False).valid


@pytest.mark.parametrize(("component_type", "expected"), [("orchestration", 0), ("AGENT", 1)])
def test_an_orchestration_nodes_script_is_not_read_for_a_record(
    tmp_path: Path, component_type: str, expected: int
) -> None:
    project = _recorded(tmp_path, _field("tags", 5))
    node = {"id": "agent", "script": "agent.py", "component_type": component_type}
    manifest = read_manifest(project / "agent.yaml")
    loaded = {"nodes": [node], "components": [{"name": "agent", "manifest_yaml": manifest}]}
    assert len(component_record_checks(str(project), loaded)) == expected


@pytest.mark.parametrize(
    "shape", [pytest.param({"crlf": True}, id="crlf"), pytest.param({"named": False}, id="unnamed")]
)
def test_a_refused_record_is_read_against_the_manifest_export_reads(
    tmp_path: Path, shape: dict[str, bool]
) -> None:
    # Ports the manifest owns: refused only while the record's digest matches
    # the manifest bytes export reads, so a different read would miss it.
    project = _recorded(tmp_path, _field("ports", "x"), **shape)
    with pytest.raises(ValidationError):
        orchestration_to_bundle(str(project))
    assert len(_record_errors(project)) == 1
    assert len(_record_errors(project, validate_scripts=True)) == 1


def test_an_edited_manifest_owns_the_fields_it_declares(tmp_path: Path) -> None:
    project = _recorded(tmp_path, _field("ports", "x"))
    manifest = project / "agent.yaml"
    manifest.write_bytes(manifest.read_bytes() + b"# edited\n")
    orchestration_to_bundle(str(project))
    assert _record_errors(project) == []
