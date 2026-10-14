"""A project on disk is judged on the component ports the platform judges.

The platform reads what a node sends downstream from its component port: the
port's direction picks the contract port, and the port's
``response_data_passthrough`` / ``response_data_passthrough_except`` decide what
it forwards (``_declares_passthrough``, ``_passthrough_exceptions``). A mainflow
AGENT has no OUT port there: ``publish`` turns its ``out`` hop into a config hop
and folds the out port's response contract into its in port, and the platform
checks the hop from that in port (``_phantom_mainflow.py::_validate_one_phantom_hop``).

Every port row here omits ``direction``, as a hand-written ``orchestration.yaml``
does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from perceivo.validator._orchestration import validate_orchestration
from perceivo.validator._orchestration._loader import _load_from_directory

_START = """
name: start
output_ports:
- port_name: output
  port_type: out
  response_data:
    q: string - The question.
  artifact_type_output: none
"""

_AGENT = """
name: agent
input_ports:
- port_name: input
  port_type: in
  request_data:
    q: string - The question.
  response_data_passthrough: request_data
{except_line}  artifact_type_input: none
  artifact_type_output: json
output_ports:
- port_name: output
  port_type: out
  response_data:
    a: string - The answer.
{out_atype_line}"""

_END = """
name: end
input_ports:
- port_name: input
  port_type: in
  request_data:
{fields}  artifact_type_input: {atype}
"""


def _port(node: str, key: str, port_type: str) -> dict[str, Any]:
    return {"id": f"{node}.{key}", "node_id": node, "name": key, "port_type": port_type}


def _project(
    tmp_path: Path,
    *,
    out_hop: bool,
    end_fields: tuple[str, ...] = ("q",),
    end_atype: str = "json",
    except_q: bool = False,
    out_atype: str | None = None,
    in_row: dict[str, Any] | None = None,
    pinned: list[dict[str, Any]] | None = None,
) -> Path:
    """START -> AGENT -> END, the AGENT continuing by an ``out`` edge or by config.

    *in_row* adds fields to the AGENT's ``input`` row; *pinned* is written as the
    AGENT node's ``component_ports``.
    """
    agent: dict[str, Any] = {"id": "agent", "component_type": "AGENT"}
    if pinned is not None:
        agent["component_ports"] = pinned
    ports = [
        _port("start", "output", "out"),
        {**_port("agent", "input", "in"), **(in_row or {})},
        _port("end", "input", "in"),
    ]
    edges = [
        {"source_port_id": "start.output", "target_port_id": "agent.input", "kind": "MAINFLOW"}
    ]
    if out_hop:
        ports.append(_port("agent", "output", "out"))
        edges.append(
            {"source_port_id": "agent.output", "target_port_id": "end.input", "kind": "MAINFLOW"}
        )
    else:
        agent["config"] = {"mainflow_target_node_ref": "end"}
    manifests = {
        "start": _START,
        "agent": _AGENT.format(
            except_line="  response_data_passthrough_except: [q]\n" if except_q else "",
            out_atype_line=f"  artifact_type_output: {out_atype}\n" if out_atype else "",
        ),
        "end": _END.format(
            fields="".join(f"    {name}: string - The {name}.\n" for name in end_fields),
            atype=end_atype,
        ),
    }
    nodes = [
        {"id": "start", "component_type": "START"},
        agent,
        {"id": "end", "component_type": "END"},
    ]
    for node in nodes:
        name = node["id"]
        node.update(script=f"{name}.py", manifest=f"{name}.yaml")
        (tmp_path / f"{name}.yaml").write_text(manifests[name], encoding="utf-8")
        (tmp_path / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    graph = {"nodes": nodes, "ports": ports, "edges": edges}
    (tmp_path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    return tmp_path


def _errors(project: Path, title: str) -> list[str]:
    report = validate_orchestration(str(project), validate_scripts=False)
    return [c.message for c in report.errors if c.title == title]


def _contract_errors(project: Path) -> list[str]:
    return _errors(project, "Port contract mismatch")


def _artifact_errors(project: Path) -> list[str]:
    return _errors(project, "Artifact type mismatch")


def test_a_port_without_direction_takes_its_component_ports(tmp_path: Path) -> None:
    """START's ``out`` row is its contract port, so ``q`` reaches the AGENT."""
    assert _contract_errors(_project(tmp_path, out_hop=False)) == []


def test_the_in_ports_passthrough_forwards_what_reached_it(tmp_path: Path) -> None:
    """The manifest's passthrough carries START's ``q`` across the AGENT to END."""
    assert _contract_errors(_project(tmp_path, out_hop=False)) == []
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    agent_in = next(p for p in loaded["ports"] if p["id"] == "agent.input")
    assert agent_in["response_data_passthrough"] == "request_data"


def test_a_passthrough_exception_is_not_forwarded(tmp_path: Path) -> None:
    errors = _contract_errors(_project(tmp_path, out_hop=False, except_q=True))
    assert len(errors) == 1
    assert "'q'" in errors[0]


def test_an_out_hop_is_checked_from_the_in_port(tmp_path: Path) -> None:
    """The ``out`` edge becomes a config hop; the in port's ``json`` meets END's."""
    project = _project(tmp_path, out_hop=True, end_fields=("q", "a"))
    assert _artifact_errors(project) == []
    loaded = _load_from_directory(str(project))
    assert loaded is not None
    agent = next(n for n in loaded["nodes"] if n["id"] == "agent")
    assert agent["config"]["mainflow_target_node_ref"] == "end"
    assert "agent.output" not in {p["id"] for p in loaded["ports"]}


def test_the_out_ports_response_folds_into_the_in_port(tmp_path: Path) -> None:
    """END gets ``q`` by passthrough and ``a`` from the folded out port."""
    assert _contract_errors(_project(tmp_path, out_hop=True, end_fields=("q", "a"))) == []


def test_a_hop_is_checked_against_the_targets_real_in_port(tmp_path: Path) -> None:
    """END's own ``text`` requirement is judged, not a synthesized stand-in's."""
    errors = _artifact_errors(
        _project(tmp_path, out_hop=True, end_fields=("q", "a"), end_atype="text")
    )
    assert len(errors) == 1
    assert "'text'" in errors[0]


def test_a_pinned_component_port_list_is_what_forwards(tmp_path: Path) -> None:
    """A node pinning ``component_ports`` bundles them verbatim, manifest aside.

    The pinned ``input`` states no passthrough, so publish sends none and ``q``
    does not reach END, although the manifest's ``input`` forwards it.
    """
    pinned = [
        {
            "key": "input",
            "direction": "IN",
            "port_type": "in",
            "request_data_schema": {"q": "string - The question."},
            "artifact_type_input": "none",
            "artifact_type_output": "json",
        },
        {
            "key": "output",
            "direction": "OUT",
            "port_type": "out",
            "response_data_schema": {"a": "string - The answer."},
        },
    ]
    project = _project(tmp_path, out_hop=True, end_fields=("q", "a"), pinned=pinned)
    errors = _contract_errors(project)
    assert len(errors) == 1
    assert "'q'" in errors[0]
    loaded = _load_from_directory(str(project))
    assert loaded is not None
    agent_in = next(p for p in loaded["ports"] if p["id"] == "agent.input")
    assert "response_data_passthrough" not in agent_in


def test_the_fold_compares_the_ports_as_written(tmp_path: Path) -> None:
    """The row's ``text`` meets the out port's ``text``, so the writer folds ``a`` in.

    The manifest overlay rewrites the row to ``json``; the writer compares the
    row as written, finds no conflict, and folds.
    """
    project = _project(
        tmp_path,
        out_hop=True,
        end_fields=("q", "a"),
        out_atype="text",
        in_row={"artifact_type_output": "text"},
    )
    report = validate_orchestration(str(project), validate_scripts=False)
    assert report.errors == []


def test_a_conflicting_fold_is_refused_and_not_folded(tmp_path: Path) -> None:
    """In ``json`` against out ``text``: the bundle is refused and ``a`` is not folded."""
    project = _project(tmp_path, out_hop=True, end_fields=("q", "a"), out_atype="text")
    assert len(_errors(project, "Conflicting response contracts on a mainflow node")) == 1
    errors = _contract_errors(project)
    assert len(errors) == 1
    assert "'a'" in errors[0]


_PINNED = [
    {
        "key": "input",
        "direction": "IN",
        "port_type": "in",
        "request_data_schema": {"q": "string - The question."},
        "artifact_type_input": "none",
        "response_data_passthrough": "request_data",
    },
    {
        "key": "output",
        "direction": "OUT",
        "port_type": "out",
        "response_data_schema": {"a": "string - The answer."},
        "artifact_type_output": "json",
    },
]
_OUT_ROW = {"response_data_schema": {"a": "string - The answer."}, "artifact_type_output": "json"}


def _same_bundle_project(
    tmp_path: Path,
    *,
    scripted: bool,
    in_row: dict[str, Any],
    out_row: dict[str, Any],
    pinned: bool = False,
    manifest_in: dict[str, Any] | None = None,
    pinned_out: dict[str, Any] | None = None,
    end_in: dict[str, Any] | None = None,
) -> Path:
    """START -> AGENT -> END by an ``out`` hop, END needing ``q``, ``a`` and ``json``.

    Unless *manifest_in* adds fields to it, the AGENT's manifest states neither a
    response nor an output type on its in port, so the bundle's folded values are
    the only ones the platform sees. *pinned_out* adds fields to the pinned out
    port, and *end_in* replaces fields of END's in port.
    """
    manifests: dict[str, dict[str, Any]] = {
        "start": {
            "name": "start",
            "output_ports": [
                {
                    "port_name": "output",
                    "port_type": "out",
                    "response_data": {"q": "string - The question."},
                    "artifact_type_output": "none",
                }
            ],
        },
        "end": {
            "name": "end",
            "input_ports": [
                {
                    "port_name": "input",
                    "port_type": "in",
                    "request_data": {"q": "string - The question.", "a": "string - The answer."},
                    "artifact_type_input": "json",
                    **(end_in or {}),
                }
            ],
        },
    }
    agent: dict[str, Any] = {"id": "agent", "component_type": "AGENT"}
    if scripted:
        manifests["agent"] = {
            "name": "agent",
            "input_ports": [
                {
                    "port_name": "input",
                    "port_type": "in",
                    "request_data": {"q": "string - The question."},
                    "artifact_type_input": "none",
                    "response_data_passthrough": "request_data",
                    **(manifest_in or {}),
                }
            ],
            "output_ports": [
                {
                    "port_name": "output",
                    "port_type": "out",
                    "response_data": {"a": "string - The answer."},
                    "artifact_type_output": "json",
                }
            ],
        }
    else:
        agent["component_asset_id"] = "00000000-0000-0000-0000-000000000001"
    if pinned:
        agent["component_ports"] = [_PINNED[0], {**_PINNED[1], **(pinned_out or {})}]
    nodes = [
        {"id": "start", "component_type": "START"},
        agent,
        {"id": "end", "component_type": "END"},
    ]
    for name, manifest in manifests.items():
        node = next(n for n in nodes if n["id"] == name)
        node.update(script=f"{name}.py", manifest=f"{name}.yaml")
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        (tmp_path / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    ports = [
        _port("start", "output", "out"),
        {**_port("agent", "input", "in"), **in_row},
        {**_port("agent", "output", "out"), **out_row},
        _port("end", "input", "in"),
    ]
    edges = [
        {"source_port_id": "start.output", "target_port_id": "agent.input", "kind": "MAINFLOW"},
        {"source_port_id": "agent.output", "target_port_id": "end.input", "kind": "MAINFLOW"},
    ]
    graph = {"nodes": nodes, "ports": ports, "edges": edges}
    (tmp_path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    return tmp_path


_SCRIPTLESS_IN = {
    "request_data_schema": {"q": {"type": "string", "required": True}},
    "response_data_passthrough": "request_data",
}


@pytest.mark.parametrize(
    ("scripted", "pinned", "in_row", "out_row"),
    [
        pytest.param(True, True, {}, {}, id="pinned"),
        pytest.param(
            True,
            True,
            {"response_data_schema": {"z": "string - Ignored."}},
            {},
            id="pinned-row-response",
        ),
        pytest.param(True, True, {"artifact_type_output": "text"}, {}, id="pinned-row-output-type"),
        pytest.param(False, False, _SCRIPTLESS_IN, _OUT_ROW, id="scriptless"),
        pytest.param(
            False,
            False,
            {**_SCRIPTLESS_IN, "direction": "in"},
            {**_OUT_ROW, "direction": "out"},
            id="scriptless-with-direction",
        ),
    ],
)
def test_projects_publish_bundles_alike_are_judged_alike(
    tmp_path: Path,
    scripted: bool,
    pinned: bool,
    in_row: dict[str, Any],
    out_row: dict[str, Any],
) -> None:
    """Each case publishes the same bundle as its control, so it gets the same verdict.

    A pinned node's bundled port ignores what its rows state, and ``publish``
    derives a row's direction whether or not the row spells it.
    """
    project = _same_bundle_project(
        tmp_path, scripted=scripted, pinned=pinned, in_row=in_row, out_row=out_row
    )
    report = validate_orchestration(str(project), validate_scripts=False)
    assert [c.message for c in report.errors] == []


_M = {"m": "string - The manifest's answer."}
_M_SCHEMA = {"m": {"type": "string"}}
_A_SCHEMA = {"a": {"type": "string"}}


@pytest.mark.parametrize(
    ("manifest_in", "pinned_out", "end_in", "error"),
    [
        pytest.param(
            {"response_data": _M},
            {},
            {"request_data": {"q": "string - The question.", **_M}},
            None,
            id="manifest-response",
        ),
        pytest.param({"response_data": _M}, {}, {}, "'a'", id="fold-response"),
        pytest.param(
            {"artifact_type_output": "text"},
            {},
            {"artifact_type_input": "text"},
            None,
            id="manifest-output-type",
        ),
        pytest.param({"artifact_type_output": "text"}, {}, {}, "text", id="fold-output-type"),
        pytest.param(
            {"artifact_output_schema": _M_SCHEMA},
            {"artifact_output_schema": _A_SCHEMA},
            {"artifact_input_schema": _M_SCHEMA},
            None,
            id="manifest-output-schema",
        ),
        pytest.param(
            {"artifact_output_schema": _M_SCHEMA},
            {"artifact_output_schema": _A_SCHEMA},
            {"artifact_input_schema": _A_SCHEMA},
            "'a'",
            id="fold-output-schema",
        ),
    ],
)
def test_a_field_the_pinned_nodes_manifest_states_wins_over_the_fold(
    tmp_path: Path,
    manifest_in: dict[str, Any],
    pinned_out: dict[str, Any],
    end_in: dict[str, Any],
    error: str | None,
) -> None:
    """The platform overlays the bundled manifest on the ports it stores, after the fold.

    So a field the manifest states on the pinned node's in port is what the in
    port forwards, not the value the out port's fold carries: END is valid when
    it needs the manifest's value and invalid when it needs the fold's.
    """
    project = _same_bundle_project(
        tmp_path,
        scripted=True,
        pinned=True,
        in_row={},
        out_row={},
        manifest_in=manifest_in,
        pinned_out=pinned_out,
        end_in=end_in,
    )
    report = validate_orchestration(str(project), validate_scripts=False)
    messages = [c.message for c in report.errors]
    if error is None:
        assert messages == []
    else:
        assert len(messages) == 1
        assert error in messages[0]
