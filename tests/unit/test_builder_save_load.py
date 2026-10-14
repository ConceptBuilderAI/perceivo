"""T4: save / load / list / export / import roundtrip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perceivo.orchestration._builder import Orchestration
from perceivo.orchestration._builder._project_io import (
    list_orchestrations,
    load,
    load_bundle,
    save_bundle,
)


def _two_node_orch() -> Orchestration:
    orch = Orchestration("twonode", description="d")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    b = orch.add_node("b", "AGENT", script="b.py", manifest="b.yaml")
    orch.add_edge(a, b)
    return orch


def test_save_writes_orchestration_yaml(tmp_workspace):
    orch = _two_node_orch()
    path = orch.save(str(tmp_workspace))
    assert path.name == "orchestration.yaml"
    assert path.exists()


def test_save_updates_project_dir(tmp_workspace):
    orch = _two_node_orch()
    orch.save(str(tmp_workspace))
    assert orch.project_dir == str(tmp_workspace.resolve())


def test_save_writes_captured_script_and_manifest_sources(tmp_workspace):
    orch = Orchestration("flow")
    n = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    n.script_source = "print('hi')\n"
    n.manifest_source = "name: a\n"
    orch.save(str(tmp_workspace))
    assert (tmp_workspace / "a.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert (tmp_workspace / "a.yaml").read_text(encoding="utf-8") == "name: a\n"


def test_load_raises_when_orchestration_yaml_missing(tmp_workspace):
    workspace_path = str(tmp_workspace)
    with pytest.raises(FileNotFoundError, match=r"orchestration.yaml not found"):
        load(workspace_path)


def test_save_load_roundtrip_preserves_name_nodes_edges(tmp_workspace):
    orch = _two_node_orch()
    orch.save(str(tmp_workspace))

    loaded = load(str(tmp_workspace))
    assert loaded.name == "twonode"
    assert loaded.description == "d"
    assert set(loaded.nodes.keys()) == set(orch.nodes.keys())
    assert len(loaded.edges) == 1
    assert loaded.edges[0].kind == "MAINFLOW"


def test_list_orchestrations_returns_each_project(tmp_workspace):
    for name in ("one", "two"):
        (tmp_workspace / name).mkdir()
        sub = Orchestration(name)
        sub.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
        sub.save(str(tmp_workspace / name))

    results = list_orchestrations(str(tmp_workspace))
    names = {r["name"] for r in results}
    assert names == {"one", "two"}
    for r in results:
        assert r["node_count"] == 1
        assert r["edge_count"] == 0


def _saved_one_node_project(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    orch = Orchestration("export_me")
    n = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    n.script_source = "async def run(input):\n    return None\n"
    n.manifest_source = "name: a\ntype: AGENT\n"
    orch.save(str(directory))
    return directory


def test_export_then_import_roundtrip(tmp_workspace: Path):
    src_dir = _saved_one_node_project(tmp_workspace / "src")

    export_file = tmp_workspace / "bundle.json"
    bundle = save_bundle(str(src_dir), output=str(export_file))
    assert bundle["export_type"] == "orchestration"
    assert export_file.exists()

    payload = json.loads(export_file.read_text(encoding="utf-8"))
    assert payload["orchestration"]["name"] == "export_me"
    # The bundle keys components by portable ref, not by source filename: the
    # importing side has no local file layout to match against.
    assert [c["name"] for c in payload["components"].values()] == ["a"]
    assert payload["components"]["comp_0"]["script"] == "async def run(input):\n    return None\n"

    dest_dir = tmp_workspace / "dest"
    dest_dir.mkdir()
    new_path = load_bundle(str(export_file), str(dest_dir))
    assert new_path.exists()
    assert (new_path / "orchestration.yaml").exists()
    assert (new_path / "comp_0.py").read_text(encoding="utf-8").startswith("async def run")


def test_export_rejects_traversal_in_the_output_path(tmp_workspace: Path):
    src_dir = _saved_one_node_project(tmp_workspace / "src")
    out = str(tmp_workspace / ".." / "escape.json")
    with pytest.raises(ValueError, match="must not contain"):
        save_bundle(str(src_dir), output=out)


def test_import_accepts_a_bundle_already_in_hand(tmp_workspace: Path):
    """The dict leg of the type dispatch: no file has to exist at all."""
    src_dir = _saved_one_node_project(tmp_workspace / "src")
    bundle = save_bundle(str(src_dir))

    out = load_bundle(bundle, str(tmp_workspace / "dest"))
    assert (out / "orchestration.yaml").exists()


def test_import_rejects_a_source_that_is_neither_path_nor_bundle(tmp_workspace):
    dest_path = str(tmp_workspace)
    with pytest.raises(TypeError, match="must be a path"):
        load_bundle(42, dest_path)


def test_import_missing_source(tmp_workspace):
    missing = str(tmp_workspace / "absent.json")
    dest_path = str(tmp_workspace)
    with pytest.raises(FileNotFoundError):
        load_bundle(missing, dest_path)


def test_import_rejects_a_non_orchestration_bundle(tmp_workspace):
    bad = tmp_workspace / "bad.json"
    bad.write_text(json.dumps({"export_type": "component"}), encoding="utf-8")
    bad_path = str(bad)
    dest_path = str(tmp_workspace)
    with pytest.raises(ValueError, match="export_type != 'orchestration'"):
        load_bundle(bad_path, dest_path)


def test_import_name_override(tmp_workspace):
    src = tmp_workspace / "src"
    src.mkdir()
    Orchestration("original").save(str(src))
    bundle = tmp_workspace / "b.json"
    save_bundle(str(src), output=str(bundle))

    dest = tmp_workspace / "dest"
    dest.mkdir()
    out = load_bundle(str(bundle), str(dest), name_override="renamed")
    assert out.name == "renamed"
    assert out.exists()
