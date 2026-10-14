"""A run's input is checked against the request data its START declares."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from perceivo._cli import main as cli
from perceivo.runner import run_orchestration

UTF8 = "utf-8"
QUERY = "query"
_START = """
class C:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=inp.artifact, response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""

_END = """
class C:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data=dict(inp.response_data or {}))
ENTRY_CLASS = "C"
"""

_DECLARED = {QUERY: {"type": "string"}, "limit": {"type": "integer", "required": False}}


def _project(
    root: Path, request_data: object, key: str = "external_in_port", as_list: bool = False
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"extension_type": "START", "name": "s"}
    if request_data is not None:
        port = {"port_name": "external_input", "request_data": request_data}
        manifest[key] = [port] if as_list else port
    (root / "s.yaml").write_text(yaml.safe_dump(manifest), encoding=UTF8)
    graph = {
        "nodes": [
            {
                "id": "s",
                "name": "s",
                "component_type": "START",
                "script": "s.py",
                "manifest": "s.yaml",
            },
            {"id": "e", "name": "e", "component_type": "END", "script": "e.py"},
        ],
        "ports": [
            {"id": "s_out", "node_id": "s", "name": "output", "port_type": "out"},
            {"id": "e_in", "node_id": "e", "name": "input", "port_type": "in"},
        ],
        "edges": [{"source_port_id": "s_out", "target_port_id": "e_in", "kind": "MAINFLOW"}],
    }
    (root / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding=UTF8)
    (root / "s.py").write_text(_START, encoding=UTF8)
    (root / "e.py").write_text(_END, encoding=UTF8)
    return root


@pytest.fixture
def declared(tmp_path) -> Path:
    return _project(tmp_path / "p", _DECLARED)


async def test_matching_input_runs(declared):
    result = await run_orchestration(str(declared), input_data={QUERY: "hi"})
    assert result.success is True, result.error


async def test_an_optional_field_may_be_supplied(declared):
    result = await run_orchestration(str(declared), input_data={QUERY: "hi", "limit": 3})
    assert result.success is True, result.error


async def test_an_unknown_key_fails_before_start_naming_the_declared_keys(declared):
    result = await run_orchestration(str(declared), input_data={QUERY: "hi", "qurey": "x"})
    assert result.success is False
    assert "unknown key(s) qurey" in result.error
    assert "Declared keys: limit, query" in result.error
    assert result.trace == []


async def test_a_missing_required_key_fails(declared):
    result = await run_orchestration(str(declared), input_data={})
    assert result.success is False
    assert "missing required key(s) query" in result.error


async def test_a_start_without_request_data_is_not_checked(tmp_path):
    project = _project(tmp_path / "p", None)
    result = await run_orchestration(str(project), input_data={"anything": 1})
    assert result.success is True, result.error


def _cli_run(project: Path, payload: str) -> int:
    return (
        CliRunner()
        .invoke(cli, ["run", "orchestration", str(project), "--input", payload])
        .exit_code
    )


async def test_the_cli_applies_the_same_check(declared):
    ok = await asyncio.to_thread(_cli_run, declared, '{"query": "hi"}')
    refused = await asyncio.to_thread(_cli_run, declared, '{"qurey": "hi"}')
    assert (ok, refused) == (0, 1)


_SHORTHAND = {
    "optional": "integer (optional) - Read when supplied",
    "described": "string - Always needed",
    "bare": "string",
    "mapping": {"type": "integer", "required": False},
}


@pytest.fixture
def shorthand(tmp_path) -> Path:
    return _project(tmp_path / "p", _SHORTHAND)


async def test_a_string_field_marked_optional_is_not_required(shorthand):
    result = await run_orchestration(str(shorthand), input_data={"described": "x", "bare": "y"})
    assert result.success is True, result.error


async def test_described_and_bare_string_fields_stay_required(shorthand):
    result = await run_orchestration(str(shorthand), input_data={"optional": 1, "mapping": 2})
    assert result.success is False
    assert result.error.endswith(
        "missing required key(s) bare, described. "
        "Declared keys: bare, described, mapping, optional (required: bare, described)."
    )


async def test_only_required_false_makes_a_mapping_field_optional(tmp_path):
    project = _project(
        tmp_path / "p",
        {"unset": {"type": "integer"}, "off": {"type": "integer", "required": False}},
    )
    refused = await run_orchestration(str(project), input_data={"off": 1})
    accepted = await run_orchestration(str(project), input_data={"unset": 1})
    assert refused.error.endswith(
        "missing required key(s) unset. Declared keys: off, unset (required: unset)."
    )
    assert accepted.success is True, accepted.error


@pytest.mark.parametrize(
    ("key", "as_list"),
    [("external_in_port", True), ("external_in_ports", True), ("external_in_ports", False)],
)
async def test_every_external_in_port_form_is_checked(tmp_path, key, as_list):
    project = _project(tmp_path / "p", _DECLARED, key=key, as_list=as_list)
    refused = await run_orchestration(str(project), input_data={"qurey": "hi"})
    accepted = await run_orchestration(str(project), input_data={QUERY: "hi"})
    assert refused.success is False
    assert "Declared keys: limit, query (required: query)." in refused.error
    assert accepted.success is True, accepted.error


def _extend_manifest(project: Path, **keys: object) -> Path:
    manifest = yaml.safe_load((project / "s.yaml").read_text(encoding=UTF8))
    manifest.update(keys)
    (project / "s.yaml").write_text(yaml.safe_dump(manifest), encoding=UTF8)
    return project


async def test_external_in_port_wins_over_external_in_ports(tmp_path):
    project = _extend_manifest(
        _project(tmp_path / "p", _DECLARED),
        external_in_ports=[{"port_name": "other", "request_data": {"other": "string"}}],
    )
    result = await run_orchestration(str(project), input_data={QUERY: "hi"})
    assert result.success is True, result.error


async def test_the_first_mapping_port_is_the_one_checked(tmp_path):
    project = _project(tmp_path / "p", None)
    manifest = yaml.safe_load((project / "s.yaml").read_text(encoding=UTF8))
    manifest["external_in_port"] = [
        "not a port",
        {"port_name": "first", "request_data": _DECLARED},
        {"port_name": "second", "request_data": {"other": "string"}},
    ]
    (project / "s.yaml").write_text(yaml.safe_dump(manifest), encoding=UTF8)
    result = await run_orchestration(str(project), input_data={"other": "x"})
    assert result.success is False
    assert "Declared keys: limit, query (required: query)." in result.error


@pytest.mark.parametrize("port_request_data", [None, {}, {"x": 5}])
async def test_request_data_schema_is_checked_when_the_port_declares_no_fields(
    tmp_path, port_request_data
):
    project = _extend_manifest(
        _project(tmp_path / "p", port_request_data), request_data_schema=_DECLARED
    )
    refused = await run_orchestration(str(project), input_data={"anything": 1})
    accepted = await run_orchestration(str(project), input_data={QUERY: "hi"})
    assert refused.error == (
        "Input does not match START 's' request_data_schema: unknown key(s) anything; "
        "missing required key(s) query. Declared keys: limit, query (required: query)."
    )
    assert accepted.success is True, accepted.error


async def test_port_request_data_wins_over_request_data_schema(tmp_path):
    project = _extend_manifest(
        _project(tmp_path / "p", _DECLARED), request_data_schema={"other": "string"}
    )
    refused = await run_orchestration(str(project), input_data={"other": "x"})
    accepted = await run_orchestration(str(project), input_data={QUERY: "hi"})
    assert refused.error.startswith("Input does not match START 's' external_in_port.request_data:")
    assert accepted.success is True, accepted.error


async def test_external_in_ports_is_named_when_it_declares_the_input(tmp_path):
    project = _project(tmp_path / "p", _DECLARED, key="external_in_ports", as_list=True)
    refused = await run_orchestration(str(project), input_data={})
    assert refused.error.startswith(
        "Input does not match START 's' external_in_ports.request_data:"
    )


async def test_a_start_declaring_neither_accepts_any_input(tmp_path):
    project = _project(tmp_path / "p", {})
    result = await run_orchestration(str(project), input_data={"anything": 1})
    assert result.success is True, result.error


@pytest.mark.parametrize("key", ["external_in_port", "external_in_ports"])
@pytest.mark.parametrize("port", [5, 2.5, True, datetime.date(2026, 1, 1)])
async def test_a_port_value_that_is_not_a_port_or_list_declares_nothing(tmp_path, key, port):
    project = _extend_manifest(_project(tmp_path / "p", None), request_data_schema=_DECLARED)
    _extend_manifest(project, **{key: port})
    result = await run_orchestration(str(project), input_data={"anything": 1})
    assert result.success is True, result.error
