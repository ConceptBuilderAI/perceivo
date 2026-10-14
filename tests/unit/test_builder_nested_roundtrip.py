"""Builder load/save keeps node config, nested projects, ports and files."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from perceivo.orchestration._builder import Orchestration
from perceivo.orchestration._builder._project_io import load
from perceivo.types.errors import ConfigurationError

_GRAPH = {
    "format": "perceivo_platform_bundle_v1",
    "runtime_config": {"cpu_request": "500m"},
    "name": "parent",
    "description": "d",
    "nodes": [
        {
            "id": "s",
            "name": "START",
            "component_type": "START",
            "script": "s.py",
            "manifest": "s.yaml",
            "position": {"x": 1, "y": 2},
        },
        {
            "id": "n",
            "name": "Nested",
            "component_type": "ORCHESTRATION",
            "project": "nested/child",
            "config": {"execution_mode": "inline", "mainflow_target_node_ref": "e"},
        },
        {"id": "e", "name": "END", "component_type": "END", "script": "sub/e.py"},
        {"id": "x", "name": "Bare", "component_type": "AGENT", "config": "junk"},
    ],
    "ports": [
        {
            "id": "p0",
            "node_id": "s",
            "key": "output",
            "direction": "out",
            "response_data_schema": {"prompt": {"type": "string"}},
        },
        {"id": "p1", "node_id": "n", "name": "orchestration", "port_type": "in"},
        {"id": "p2", "node_id": "e", "name": "input", "port_type": "in"},
    ],
    "edges": [{"source_port_id": "p0", "target_port_id": "p1", "kind": "MAINFLOW", "label": "go"}],
}


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "orchestration.yaml").write_text(yaml.safe_dump(_GRAPH, sort_keys=False), "utf-8")
    (root / "s.py").write_text("# start\n", "utf-8")
    (root / "s.yaml").write_text("name: s\n", "utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "e.py").write_text("# end\n", "utf-8")
    child = root / "nested" / "child"
    child.mkdir(parents=True)
    (child / "orchestration.yaml").write_text("nodes: []\n", "utf-8")
    (child / "a.py").write_text("# child\n", "utf-8")
    return root


def test_load_keeps_config_project_ports_and_extras(tmp_path):
    orch = load(str(_project(tmp_path / "src")))

    nested = orch.nodes["n"]
    assert nested.project == "nested/child"
    assert nested.config == {"execution_mode": "inline", "mainflow_target_node_ref": "e"}
    assert orch.nodes["s"].extra == {"position": {"x": 1, "y": 2}}
    assert orch.nodes["x"].config == {}
    start_port = orch.nodes["s"].ports[0]
    assert start_port["id"] == "p0"
    assert start_port["name"] == "output"
    assert start_port["port_type"] == "out"
    assert start_port["response_data_schema"] == {"prompt": {"type": "string"}}
    # a node the file gives no ports keeps its type defaults
    assert [p["name"] for p in orch.nodes["x"].ports] == ["input", "output"]
    assert orch._port_registry["p1"] == "n"
    assert orch.edges[0].extra == {"label": "go"}
    assert orch.extra == {
        "format": "perceivo_platform_bundle_v1",
        "runtime_config": {"cpu_request": "500m"},
    }


def test_save_elsewhere_round_trips_the_graph_and_carries_its_files(tmp_path):
    orch = load(str(_project(tmp_path / "src")))
    dest = tmp_path / "dest"

    orch.save(str(dest))

    saved = yaml.safe_load((dest / "orchestration.yaml").read_text("utf-8"))
    assert saved["format"] == "perceivo_platform_bundle_v1"
    assert saved["runtime_config"] == {"cpu_request": "500m"}
    nodes = {n["id"]: n for n in saved["nodes"]}
    assert nodes["n"]["project"] == "nested/child"
    assert nodes["n"]["config"]["mainflow_target_node_ref"] == "e"
    assert nodes["s"]["position"] == {"x": 1, "y": 2}
    ports = {p["id"]: p for p in saved["ports"]}
    assert ports["p0"]["response_data_schema"] == {"prompt": {"type": "string"}}
    assert saved["edges"] == [
        {"source_port_id": "p0", "target_port_id": "p1", "kind": "MAINFLOW", "label": "go"}
    ]
    assert (dest / "s.py").read_text("utf-8") == "# start\n"
    assert (dest / "s.yaml").exists()
    assert (dest / "sub" / "e.py").exists()
    assert (dest / "nested" / "child" / "a.py").exists()
    # and it loads back to the same graph
    again = load(str(dest))
    assert again.nodes["n"].config == orch.nodes["n"].config
    assert [p["id"] for p in again.nodes["s"].ports] == ["p0"]


def test_save_skips_paths_that_escape_the_project(tmp_path):
    src = _project(tmp_path / "src")
    (tmp_path / "secret.py").write_text("# outside\n", "utf-8")
    orch = load(str(src))
    orch.nodes["s"].script = "../secret.py"
    orch.nodes["n"].project = "../../elsewhere"

    orch.save(str(tmp_path / "dest"))

    assert not (tmp_path / "dest" / "secret.py").exists()
    assert not (tmp_path / "elsewhere").exists()


def test_save_does_not_overwrite_an_existing_destination_file(tmp_path):
    orch = load(str(_project(tmp_path / "src")))
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "s.py").write_text("# mine\n", "utf-8")

    orch.save(str(dest))

    assert (dest / "s.py").read_text("utf-8") == "# mine\n"


def test_builder_adds_a_nested_orchestration_node(tmp_path):
    orch = Orchestration("built")
    start = orch.add_node("start", "START", script="start.py")
    nested = orch.add_node(
        "child",
        "ORCHESTRATION",
        project="nested/child",
        config={"execution_mode": "inline", "mainflow_target_node_ref": "node_end"},
    )
    orch.add_node("end", "END", script="end.py")
    orch.add_edge(start, nested)

    assert [(p["name"], p["port_type"]) for p in nested.ports] == [("orchestration", "in")]
    orch.save(str(tmp_path))
    saved = yaml.safe_load((tmp_path / "orchestration.yaml").read_text("utf-8"))
    node = next(n for n in saved["nodes"] if n["id"] == "node_child")
    assert node["project"] == "nested/child"
    assert node["config"]["execution_mode"] == "inline"
    assert "script" not in node


def test_add_edge_from_a_nested_node_points_at_its_config(tmp_path):
    orch = Orchestration("built")
    nested = orch.add_node("child", "ORCHESTRATION", project="nested/child")
    end = orch.add_node("end", "END", script="end.py")

    with pytest.raises(ConfigurationError, match="mainflow_target_node_ref"):
        orch.add_edge(nested, end)


def test_auto_endpoints_count_a_config_hop_as_wired():
    orch = Orchestration("built")
    orch.add_node("a", "AGENT", script="a.py")
    orch.add_node(
        "child", "ORCHESTRATION", project="c", config={"mainflow_target_node_ref": "node_a"}
    )

    orch._auto_inject_endpoints()

    wired_from_start = {
        orch._port_registry[e.target_port_id]
        for e in orch.edges
        if orch._port_registry[e.source_port_id] == "auto_start"
    }
    wired_to_end = {
        orch._port_registry[e.source_port_id]
        for e in orch.edges
        if orch._port_registry[e.target_port_id] == "auto_end"
    }
    assert wired_from_start == {"node_child"}
    assert wired_to_end == {"node_a"}
