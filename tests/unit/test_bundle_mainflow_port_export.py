"""The exported ``ports[]`` list must resolve against the components it names.

The writer must not emit the bundle reader's *synthesized* mainflow out-port —
``{"key": "output", "direction": "OUT"}``, marked ``virtual`` locally and
unmarkable in the bundle — for every node type except AGENT and TOOL. The
platform importer resolves each port row strictly against its node's component
(``_orchestration_import_payload._resolve_port_ref``), so an FSM or layer
component whose real OUT ports are typed ``state``/``policy`` that row fails
the import with ``Could not resolve port key='output'
direction='OUT' on ref 'comp_N'``. Nothing local fails: the synthesis happens
before validation and ``perceivo.validator._orchestration._edges`` exempts the
port by its ``virtual`` flag.

:func:`unresolvable_port_rows` is that server rule, so these tests fail for the
reason the platform fails rather than for a local proxy of it.

The fixture below is the shape of the platform's own export of an
error-trapping orchestration: an Error Handler FSM declaring ``input`` IN and
``primary``/``fallback``/``policy`` OUT, three INVOKE edges leaving those OUT
ports, no ``output`` port, no MAINFLOW edge leaving the node, and the mainflow
next hop carried in ``config.mainflow_target_node_ref``. Re-exporting the
imported project has to reproduce exactly that — not pick one of the
INVOKE-wired state ports, which would invent wiring the platform never had.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._bundle.schema import OrchestrationExport
from perceivo.orchestration._builder._bundle_project import materialize_platform_bundle


def unresolvable_port_rows(bundle: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Port rows the platform importer could not resolve: ``(ref, key, direction)``.

    Mirrors ``_orchestration_import_payload._resolve_port_ref`` ->
    ``_orchestration_import._load_port_key_map``: resolve ``node_ref`` to the
    node's ``component_ref``, then require ``components[ref]["ports"]`` to hold an
    entry with the same key and direction. Nodes carrying an ``orchestration_ref``
    resolve against the child orchestration's own generated rows instead and are
    skipped, exactly as the server skips them.
    """
    nodes = {n["local_id"]: n for n in bundle.get("nodes", [])}
    declared = {
        ref: {
            (p.get("key"), str(p.get("direction") or "").upper()) for p in comp.get("ports") or []
        }
        for ref, comp in (bundle.get("components") or {}).items()
    }
    misses = []
    for row in bundle.get("ports") or []:
        node = nodes.get(row["node_ref"], {})
        if node.get("orchestration_ref"):
            continue
        ref = node.get("component_ref", "")
        key = row["component_port_key"]
        direction = str(row["component_port_direction"]).upper()
        if (key, direction) not in declared.get(ref, set()):
            misses.append((ref, key, direction))
    return misses


def _component(name: str, category: str, ports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "script": f"# {name}\n",
        "manifest_yaml": f"extension_type: {category}\nname: {name}\n",
        "ports": [
            {"key": key, "direction": direction, "port_type": port_type}
            for key, direction, port_type in (
                (p["key"], p["direction"], p["port_type"]) for p in ports
            )
        ],
    }


def _port(key: str, direction: str, port_type: str) -> dict[str, str]:
    return {"key": key, "direction": direction, "port_type": port_type}


def _fsm_platform_bundle() -> dict[str, Any]:
    """A platform export whose FSM node continues the mainflow through config."""
    return {
        "export_version": "1.0",
        "export_type": "orchestration",
        "orchestration": {"name": "error-trapping", "description": "fixture"},
        "components": {
            "c_start": _component(
                "Start",
                "START",
                [_port("trigger", "IN", "external_in"), _port("output", "OUT", "out")],
            ),
            "c_fsm": _component(
                "Error Handler",
                "FSM",
                [
                    _port("input", "IN", "in"),
                    _port("primary", "OUT", "state"),
                    _port("fallback", "OUT", "state"),
                    _port("policy", "OUT", "policy"),
                ],
            ),
            "c_policy": _component("Policy", "AGENT", [_port("input", "IN", "in")]),
            "c_fail": _component("Always Fail", "AGENT", [_port("input", "IN", "in")]),
            "c_delay": _component("Delay", "AGENT", [_port("input", "IN", "in")]),
            "c_end": _component(
                "End", "END", [_port("input", "IN", "in"), _port("result", "OUT", "external_out")]
            ),
        },
        "nodes": [
            {"local_id": "node_0", "name": "START", "component_ref": "c_start", "config": {}},
            {
                "local_id": "node_1",
                "name": "Error Handler",
                "component_ref": "c_fsm",
                "config": {"mainflow_target_node_ref": "node_5"},
            },
            {"local_id": "node_2", "name": "Policy", "component_ref": "c_policy", "config": {}},
            {"local_id": "node_3", "name": "Always Fail", "component_ref": "c_fail", "config": {}},
            {"local_id": "node_4", "name": "Delay", "component_ref": "c_delay", "config": {}},
            {"local_id": "node_5", "name": "END", "component_ref": "c_end", "config": {}},
        ],
        "ports": [
            {
                "node_ref": "node_0",
                "component_port_key": "trigger",
                "component_port_direction": "IN",
            },
            {
                "node_ref": "node_0",
                "component_port_key": "output",
                "component_port_direction": "OUT",
            },
            {"node_ref": "node_1", "component_port_key": "input", "component_port_direction": "IN"},
            {
                "node_ref": "node_1",
                "component_port_key": "policy",
                "component_port_direction": "OUT",
            },
            {"node_ref": "node_2", "component_port_key": "input", "component_port_direction": "IN"},
            {
                "node_ref": "node_1",
                "component_port_key": "primary",
                "component_port_direction": "OUT",
            },
            {"node_ref": "node_3", "component_port_key": "input", "component_port_direction": "IN"},
            {
                "node_ref": "node_1",
                "component_port_key": "fallback",
                "component_port_direction": "OUT",
            },
            {"node_ref": "node_4", "component_port_key": "input", "component_port_direction": "IN"},
            {"node_ref": "node_5", "component_port_key": "input", "component_port_direction": "IN"},
            {
                "node_ref": "node_5",
                "component_port_key": "result",
                "component_port_direction": "OUT",
            },
        ],
        "edges": [
            {"source_port_index": 1, "target_port_index": 2, "edge_kind": "MAINFLOW"},
            {"source_port_index": 3, "target_port_index": 4, "edge_kind": "INVOKE"},
            {"source_port_index": 5, "target_port_index": 6, "edge_kind": "INVOKE"},
            {"source_port_index": 7, "target_port_index": 8, "edge_kind": "INVOKE"},
        ],
    }


def _agent_platform_bundle() -> dict[str, Any]:
    """START -> AGENT -> END, every OUT port typed ``out``: nothing is synthesized."""
    return {
        "export_version": "1.0",
        "export_type": "orchestration",
        "orchestration": {"name": "simple-agent", "description": "fixture"},
        "components": {
            "c_start": _component(
                "Start",
                "START",
                [_port("trigger", "IN", "external_in"), _port("output", "OUT", "out")],
            ),
            "c_agent": _component(
                "Agent", "AGENT", [_port("input", "IN", "in"), _port("output", "OUT", "out")]
            ),
            "c_end": _component(
                "End", "END", [_port("input", "IN", "in"), _port("result", "OUT", "external_out")]
            ),
        },
        "nodes": [
            {"local_id": "node_0", "name": "START", "component_ref": "c_start", "config": {}},
            {"local_id": "node_1", "name": "Agent", "component_ref": "c_agent", "config": {}},
            {"local_id": "node_2", "name": "END", "component_ref": "c_end", "config": {}},
        ],
        "ports": [
            {
                "node_ref": "node_0",
                "component_port_key": "trigger",
                "component_port_direction": "IN",
            },
            {
                "node_ref": "node_0",
                "component_port_key": "output",
                "component_port_direction": "OUT",
            },
            {"node_ref": "node_1", "component_port_key": "input", "component_port_direction": "IN"},
            {
                "node_ref": "node_1",
                "component_port_key": "output",
                "component_port_direction": "OUT",
            },
            {"node_ref": "node_2", "component_port_key": "input", "component_port_direction": "IN"},
            {
                "node_ref": "node_2",
                "component_port_key": "result",
                "component_port_direction": "OUT",
            },
        ],
        "edges": [
            {"source_port_index": 1, "target_port_index": 2, "edge_kind": "MAINFLOW"},
            {"source_port_index": 3, "target_port_index": 4, "edge_kind": "MAINFLOW"},
        ],
    }


def _rows(bundle: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (r["node_ref"], r["component_port_key"], r["component_port_direction"])
        for r in bundle["ports"]
    ]


def _round_trip(tmp_path: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    project = materialize_platform_bundle(bundle, tmp_path / "project")
    return orchestration_to_bundle(project)


# --------------------------------------------------------------------------- #
# (a) the defect: an FSM node whose OUT ports are all state/policy             #
# --------------------------------------------------------------------------- #


def test_fsm_mainflow_hop_does_not_export_a_fabricated_port(tmp_path: Path) -> None:
    """Every exported port row resolves; the fabricated ``output`` row is gone.

    Against the unmodified writer this fails with one miss,
    ``('comp_1', 'output', 'OUT')`` — the reader's synthesized port, which is the
    exact row the platform rejected.
    """
    again = _round_trip(tmp_path, _fsm_platform_bundle())

    assert unresolvable_port_rows(again) == []
    assert ("node_1", "output", "OUT") not in _rows(again)


def test_fsm_mainflow_hop_reproduces_the_platform_wiring(tmp_path: Path) -> None:
    """The re-export is the platform's own graph: same rows, same edges, same config.

    In particular the mainflow hop stays in ``config.mainflow_target_node_ref``
    and is *not* rerouted through one of the INVOKE-wired state ports, which is
    what selecting "the first declared OUT port" would have produced.
    """
    original = _fsm_platform_bundle()
    again = _round_trip(tmp_path, original)
    OrchestrationExport.model_validate(again)

    assert _rows(again) == _rows(original)
    assert again["edges"] == original["edges"]

    node_1 = next(n for n in again["nodes"] if n["local_id"] == "node_1")
    assert node_1["config"]["mainflow_target_node_ref"] == "node_5"
    invoke_sources = {
        again["ports"][e["source_port_index"]]["component_port_key"]
        for e in again["edges"]
        if e["edge_kind"] == "INVOKE"
    }
    assert invoke_sources == {"primary", "fallback", "policy"}


def test_reader_still_synthesizes_a_walkable_local_edge(tmp_path: Path) -> None:
    """The synthesized port is kept locally — it is the hop's only local spelling.

    Dropping it at import would leave the FSM node's mainflow continuation
    invisible to the runner's graph walk. It is marked ``virtual`` and removed
    only at the writer boundary.
    """
    import yaml

    project = materialize_platform_bundle(_fsm_platform_bundle(), tmp_path / "project")
    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))

    synthesized = [p for p in data["ports"] if p.get("virtual")]
    assert [(p["node_id"], p["key"], p["direction"]) for p in synthesized] == [
        ("node_1", "output", "out")
    ]
    by_id = {p["id"]: p for p in data["ports"]}
    walked = [
        (by_id[e["source_port_id"]]["node_id"], by_id[e["target_port_id"]]["node_id"])
        for e in data["edges"]
        if e.get("kind") == "MAINFLOW"
    ]
    assert ("node_1", "node_5") in walked


# --------------------------------------------------------------------------- #
# (b) any other undeclared port row is refused by the writer, not by the server #
# --------------------------------------------------------------------------- #


def test_writer_refuses_a_port_row_the_component_does_not_declare(tmp_path: Path) -> None:
    bundle = _fsm_platform_bundle()
    # A node-level port the component never declares: the shape that reaches the
    # platform as a 400 naming neither the orchestration nor a local file.
    bundle["ports"].append(
        {"node_ref": "node_2", "component_port_key": "ghost", "component_port_direction": "OUT"}
    )
    project = materialize_platform_bundle(bundle, tmp_path / "project")

    with pytest.raises(BundleConfigError) as caught:
        orchestration_to_bundle(project)

    message = str(caught.value)
    assert "'node_2'" in message
    assert "key='ghost'" in message
    assert "direction='OUT'" in message
    assert "('input', 'IN')" in message


def test_a_pinned_component_ports_list_that_omits_a_node_port_is_named(tmp_path: Path) -> None:
    """The message has to name the cause, which is not visible from the failing node.

    This is the shape that broke two public example packages: an AGENT component
    declaring only ``input`` IN, a node pinning that list as ``component_ports``,
    and an orphan ``output`` OUT row in the orchestration's ``ports``. Because
    the node pins ``component_ports``, the writer takes the component's port set
    verbatim and the node-declared port never reaches it — so the row is emitted
    and cannot resolve. Naming only the node and the key sends the reader to the
    node, where nothing is wrong.
    """
    bundle = _agent_platform_bundle()
    bundle["components"]["c_agent"]["ports"] = [_port("input", "IN", "in")]
    bundle["ports"] = [
        row
        for row in bundle["ports"]
        if not (row["node_ref"] == "node_1" and row["component_port_key"] == "output")
    ]
    bundle["edges"] = [{"source_port_index": 1, "target_port_index": 2, "edge_kind": "MAINFLOW"}]
    # The divergence: a row for a port the pinned component_ports does not carry.
    bundle["ports"].append(
        {"node_ref": "node_1", "component_port_key": "output", "component_port_direction": "OUT"}
    )
    project = materialize_platform_bundle(bundle, tmp_path / "project")

    with pytest.raises(BundleConfigError) as caught:
        orchestration_to_bundle(project)

    message = str(caught.value)
    assert "'node_1'" in message
    assert "key='output'" in message
    assert "direction='OUT'" in message
    assert "('input', 'IN')" in message
    # The cause, and both ways out — neither is inferable from the node alone.
    assert "component_ports" in message
    assert "add" in message
    assert "remove its row" in message


@pytest.mark.parametrize(
    "spelling, value",
    [
        ("mainflow_target_node_ref", "node_6"),
        ("mainflow_target_node_id", 6),
        ("mainflow_target_node_index", 6),
    ],
)
def test_mainflow_edge_into_a_synthesized_target_keeps_the_hop(
    tmp_path: Path, spelling: str, value: object
) -> None:
    """A target node with no declared in-port keeps its hop in config, not nowhere.

    The reader synthesizes an ``input`` port for such a target. It is not
    exportable either, so the edge goes — but the hop is written to the source
    node's config instead of being dropped by the edge indexer in silence.

    Parametrized over all three spellings the reader accepts, because the ``_ref``
    case alone cannot see the write: the reader carries node config through
    verbatim, so ``_ref`` is already present in the export whether or not the
    writer writes it. Only ``_id`` and ``_index`` distinguish the two — the writer
    normalizes them to the platform's ``_ref`` spelling, and nothing else does.
    """
    bundle = _fsm_platform_bundle()
    # An END must keep its IN port, so the in-port-less target is an AGENT.
    bundle["components"]["c_sink"] = _component("Sink", "AGENT", [])
    bundle["nodes"].append(
        {"local_id": "node_6", "name": "Sink", "component_ref": "c_sink", "config": {}}
    )
    bundle["edges"] = [e for e in bundle["edges"] if e["edge_kind"] != "MAINFLOW"]
    bundle["nodes"][0]["config"] = {spelling: value}

    again = _round_trip(tmp_path, bundle)

    assert unresolvable_port_rows(again) == []
    node_0 = next(n for n in again["nodes"] if n["local_id"] == "node_0")
    assert node_0["config"]["mainflow_target_node_ref"] == "node_6"


# --------------------------------------------------------------------------- #
# (c) shapes that already resolved must not move                               #
# --------------------------------------------------------------------------- #


def test_agent_shapes_that_already_resolved_are_unchanged(tmp_path: Path) -> None:
    """An AGENT chain, whose mainflow was already translated, exports identically.

    This is the simple-chatgpt-agent shape: every OUT port is typed ``out``, so
    no port is synthesized and no branch added here can run.
    """
    first = _round_trip(tmp_path, _agent_platform_bundle())
    second = _round_trip(tmp_path / "again", first)

    assert unresolvable_port_rows(first) == []
    assert unresolvable_port_rows(second) == []
    # Stable under repetition: the writer's output is its own fixed point.
    assert json.dumps(_rows(second)) == json.dumps(_rows(first))
    assert second["edges"] == first["edges"]
