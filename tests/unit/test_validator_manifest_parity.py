"""Validation judges the manifest export reads, and refuses what export refuses.

Each layout here is one where the validator and ``perceivo export`` used to
read different files, or where export raised on a manifest field the validator
passed.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle.component import BundleConfigError, component_to_bundle
from perceivo._cli import main as perceivo_cli
from perceivo._cli.export_cmd import _detect_and_build
from perceivo.types.errors import ConfigurationError
from perceivo.validator import validate_component, validate_orchestration
from perceivo.validator._orchestration._loader import _load_from_directory
from perceivo.validator._paths import validate_component_files
from tests.unit.test_validator_orchestration_parity import _project

SCRIPT = """
import perceivo
from perceivo.primitives import ToolInput, ToolOutput

@perceivo.tool
async def shield(input: ToolInput) -> ToolOutput:
    return ToolOutput(response_data={})
"""

HEAD = "name: Shield\nextension_type: tool\n"
PORTS = "input_ports:\n- port_name: input\n  port_type: in\n"

SHAPES = {
    "valid": HEAD + PORTS,
    "tags not a list": HEAD + PORTS + "tags: 5\n",
    "tags a list": HEAD + PORTS + "tags: [a, b]\n",
    "description not a string": HEAD + PORTS + "description: 5\n",
    "short_description a list": HEAD + PORTS + "short_description: [x]\n",
    "additional_packages not a list": HEAD + PORTS + "additional_packages: 5\n",
    "config a string": HEAD + PORTS + "config: x\n",
    "config null": HEAD + PORTS + "config: null\n",
    "config a mapping": HEAD + PORTS + "config: {k: 1}\n",
    "input_ports a number": HEAD + "input_ports: 5\n",
    "fsm_states a number": HEAD + PORTS + "fsm_states: 5\n",
    "fsm_states mixed": HEAD + PORTS + "fsm_states: [a, {key: b}, 7]\n",
    "port label not a string": HEAD + PORTS + "output_ports:\n- port_name: out\n  description: 5\n",
    "requires_gpu a string": HEAD + PORTS + "requires_gpu: 'yes'\n",
}

#: Manifests that parse to something other than a mapping.
NOT_MAPPINGS = {
    "zero": "0\n",
    "false": "false\n",
    "comment only": "# nothing here\n",
    "empty": "",
    "a list": "[]\n",
}


def _component(tmp_path: Path, manifest: str | None, name: str = "shield") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / f"{name}.py"
    script.write_text(SCRIPT, encoding="utf-8")
    if manifest is not None:
        script.with_suffix(".yaml").write_bytes(manifest.encode("utf-8"))
    return script


def _ids(report) -> list[str]:
    return [c.id for c in report.checks if c.severity == "error"]


def _exports(script: Path) -> bool:
    try:
        component_to_bundle(script)
    except (ValueError, TypeError):
        return False
    return True


@pytest.mark.parametrize("shape", sorted({**SHAPES, **NOT_MAPPINGS}))
def test_validate_passes_exactly_what_export_ships(tmp_path: Path, shape: str) -> None:
    script = _component(tmp_path, {**SHAPES, **NOT_MAPPINGS}[shape])
    assert validate_component(script).valid is _exports(script)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_manifest_field_export_refuses_is_refused(tmp_path: Path, shape: str) -> None:
    script = _component(tmp_path, SHAPES[shape])
    refused = "MAN-FIELD-001" in _ids(validate_component(script))
    assert refused is not _exports(script)


@pytest.mark.parametrize("shape", sorted(NOT_MAPPINGS))
def test_a_manifest_that_is_not_a_mapping_is_refused_by_both(tmp_path: Path, shape: str) -> None:
    script = _component(tmp_path, NOT_MAPPINGS[shape])
    with pytest.raises(BundleConfigError, match=r"shield\.yaml: a manifest must be a YAML mapping"):
        component_to_bundle(script)
    assert {"MAN-FORMAT-001", "MAN-EMPTY-001"} & set(_ids(validate_component(script)))


def test_the_refusal_names_the_field(tmp_path: Path) -> None:
    report = validate_component(_component(tmp_path, SHAPES["input_ports a number"]))
    [check] = [c for c in report.checks if c.id == "MAN-FIELD-001"]
    assert "input_ports must be a list, not int" in check.message


def test_the_refusal_names_the_field_export_failed_on(tmp_path: Path) -> None:
    # Export never reads foo_ports, so the date in fsm_states is what it raised on.
    manifest = HEAD + PORTS + "foo_ports: 3\nfsm_states: 2024-01-01\n"
    report = validate_component(_component(tmp_path, manifest))
    [check] = [c for c in report.checks if c.id == "MAN-FIELD-001"]
    assert "fsm_states must be a list, not date" in check.message


def test_of_two_bad_keys_the_one_export_reads_first_is_named(tmp_path: Path) -> None:
    manifest = HEAD + "input_ports: 5\ntool_ports: 3\n"
    script = _component(tmp_path, manifest)
    with pytest.raises(TypeError):
        component_to_bundle(script)
    [check] = [c for c in validate_component(script).checks if c.id == "MAN-FIELD-001"]
    assert "tool_ports must be a list, not int" in check.message


def test_a_manifest_yaml_beside_the_script_is_not_read(tmp_path: Path) -> None:
    # Export reads <script>.yaml only; a file it never ships cannot judge it.
    script = _component(tmp_path, None)
    (tmp_path / "manifest.yaml").write_text(HEAD + "ports:\n- port_name: input\n", encoding="utf-8")
    assert component_to_bundle(script)["component"]["manifest_yaml"] is None
    assert "MAN-PORTS-001" not in _ids(validate_component(script))


def test_a_directory_of_two_scripts_is_refused_as_export_refuses(tmp_path: Path) -> None:
    _component(tmp_path, HEAD + PORTS, name="script")
    _component(tmp_path, None, name="helper")
    with pytest.raises(click.ClickException, match=r"2 \.py files found"):
        _detect_and_build(tmp_path)
    with pytest.raises(ConfigurationError, match="exactly one"):
        validate_component(tmp_path)


def _orchestration(tmp_path: Path, manifest_line: str) -> None:
    (tmp_path / "orchestration.yaml").write_text(
        "nodes:\n  - id: a\n    component_type: TOOL\n    script: shield.py\n" + manifest_line,
        encoding="utf-8",
    )


def test_a_node_naming_no_manifest_reads_the_one_export_reads(tmp_path: Path) -> None:
    script = _component(tmp_path, HEAD + PORTS)
    _orchestration(tmp_path, "")
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    exported = component_to_bundle(script)["component"]["manifest_yaml"]
    assert exported == HEAD + PORTS
    assert loaded["components"][0]["manifest_yaml"] == exported


def test_a_node_manifest_is_read_as_export_reads_it(tmp_path: Path) -> None:
    # Bytes, so a CRLF manifest reaches the checks as export ships it.
    crlf = (HEAD + PORTS).replace("\n", "\r\n")
    script = _component(tmp_path, crlf)
    _orchestration(tmp_path, "    manifest: shield.yaml\n")
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    exported = component_to_bundle(script)["component"]["manifest_yaml"]
    assert exported == crlf
    assert loaded["components"][0]["manifest_yaml"] == exported


def _unreadable(tmp_path: Path, kind: str) -> Path:
    script = _component(tmp_path, None)
    if kind == "not utf-8":
        script.with_suffix(".yaml").write_bytes(b"name: \xff\xfe\n")
    else:
        script.with_suffix(".yaml").mkdir()
    return script


UNREADABLE = {"not utf-8": "is not UTF-8 text", "a directory": "is a directory"}


@pytest.mark.parametrize("kind", sorted(UNREADABLE))
def test_an_unreadable_manifest_is_reported_not_raised(tmp_path: Path, kind: str) -> None:
    script = _unreadable(tmp_path, kind)
    with pytest.raises(BundleConfigError, match=UNREADABLE[kind]):
        component_to_bundle(script)
    [check] = [c for c in validate_component(script).checks if c.id == "MAN-FORMAT-001"]
    assert f"shield.yaml {UNREADABLE[kind]}" in check.message

    result = CliRunner().invoke(perceivo_cli, ["validate", "component", str(script)])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert UNREADABLE[kind] in result.output


@pytest.mark.parametrize("kind", sorted(UNREADABLE))
def test_an_unreadable_node_manifest_is_reported_not_raised(tmp_path: Path, kind: str) -> None:
    _unreadable(tmp_path, kind)
    _orchestration(tmp_path, "")
    report = validate_orchestration(tmp_path)
    assert any(
        c.id.endswith("/MAN-FORMAT-001") and UNREADABLE[kind] in c.message for c in report.checks
    )

    result = CliRunner().invoke(perceivo_cli, ["validate", "orchestration", str(tmp_path)])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert UNREADABLE[kind] in result.output


def test_a_named_manifest_that_does_not_exist_is_read_as_none_by_all(tmp_path: Path) -> None:
    script = _component(tmp_path, None)
    missing = tmp_path / "missing.yaml"
    assert not component_to_bundle(script, manifest_path=missing)["component"]["manifest_yaml"]
    report = validate_component_files(script, missing)
    assert not [c for c in report.checks if c.category == "manifest"]

    result = CliRunner().invoke(perceivo_cli, ["validate", "component", str(script), str(missing)])
    assert "cannot be read" not in result.output
    assert result.exit_code == (0 if report.valid else 1)


def test_each_manifest_refusal_has_its_own_rule_and_suggestion(tmp_path: Path) -> None:
    [listed] = [c for c in _checks(_component(tmp_path / "l", "[]\n")) if c.id == "MAN-FORMAT-001"]
    [unread] = [
        c for c in _checks(_unreadable(tmp_path / "u", "a directory")) if c.id == "MAN-FORMAT-001"
    ]
    assert listed.title != unread.title
    assert "mapping" in listed.message and "key: value" in (listed.suggestion or "")
    assert "UTF-8" in (unread.suggestion or "")


def _checks(script: Path):
    return validate_component(script).checks


#: The AGENT's ports, declared in full by the graph so nothing else refuses it.
_IN = {
    "request_data_schema": {"q": "string - The question."},
    "response_data_passthrough": "request_data",
    "artifact_type_input": "none",
    "artifact_type_output": "json",
}
_OUT = {"response_data_schema": {"a": "string - The answer."}}


def _declared(tmp_path: Path, declared: str) -> Path:
    if declared == "pinned":
        pinned = [
            {"key": "input", "direction": "IN", "port_type": "in", **_IN},
            {"key": "output", "direction": "OUT", "port_type": "out", **_OUT},
        ]
        return _project(tmp_path, out_hop=True, pinned=pinned)
    project = _project(tmp_path, out_hop=True, in_row=_IN)
    graph = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    next(row for row in graph["ports"] if row["id"] == "agent.output").update(_OUT)
    (project / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    return project


@pytest.mark.parametrize("declared", ["rows", "pinned"])
def test_a_fully_declared_node_passes_without_scripts(tmp_path: Path, declared: str) -> None:
    assert validate_orchestration(_declared(tmp_path, declared), validate_scripts=False).valid


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts", "no-scripts"])
@pytest.mark.parametrize("kind", sorted(UNREADABLE))
@pytest.mark.parametrize("declared", ["rows", "pinned"])
def test_an_unreadable_manifest_is_refused_however_ports_are_declared(
    tmp_path: Path, declared: str, kind: str, scripts: bool
) -> None:
    project = _declared(tmp_path, declared)
    manifest = project / "agent.yaml"
    if kind == "not utf-8":
        manifest.write_bytes(b"# caf\xe9\n" + manifest.read_bytes())
    else:
        manifest.unlink()
        manifest.mkdir()
    with pytest.raises(BundleConfigError, match=UNREADABLE[kind]):
        _detect_and_build(project)
    report = validate_orchestration(project, validate_scripts=scripts)
    assert [c.id for c in report.checks if c.id.endswith("/MAN-FORMAT-001")] == [
        "agent.py/MAN-FORMAT-001"
    ]
    args = ["validate", "orchestration", str(project), *([] if scripts else ["--no-scripts"])]
    result = CliRunner().invoke(perceivo_cli, args)
    assert result.exit_code == 1 and UNREADABLE[kind] in result.output


#: Node manifests export refuses though it can read them, and the one rule each breaks.
REFUSED = {
    "a list": ("[]\n", "MAN-FORMAT-001"),
    "empty": ("", "MAN-EMPTY-001"),
    "comment only": ("# nothing here\n", "MAN-FORMAT-001"),
    "zero": ("0\n", "MAN-FORMAT-001"),
    "tags an int": (None, "MAN-FIELD-001"),
}


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts", "no-scripts"])
@pytest.mark.parametrize("shape", sorted(REFUSED))
@pytest.mark.parametrize("declared", ["rows", "pinned"])
def test_a_manifest_export_refuses_is_refused_however_ports_are_declared(
    tmp_path: Path, declared: str, shape: str, scripts: bool
) -> None:
    project = _declared(tmp_path, declared)
    manifest = project / "agent.yaml"
    text, rule = REFUSED[shape]
    if text is None:
        text = manifest.read_text(encoding="utf-8") + "tags: 5\n"
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        _detect_and_build(project)
    report = validate_orchestration(project, validate_scripts=scripts)
    assert [c.id for c in report.checks if c.id.startswith("agent.py/MAN-")] == [f"agent.py/{rule}"]
    args = ["validate", "orchestration", str(project), *([] if scripts else ["--no-scripts"])]
    assert CliRunner().invoke(perceivo_cli, args).exit_code == 1
