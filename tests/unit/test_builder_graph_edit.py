"""Builder graph editing: remove nodes/edges, embed a child, library START/END.

The end-to-end case is the refactor these exist for: build a parent, extract a
node into a child orchestration, save, run, export, re-import, and run again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import perceivo.orchestration as orch_api
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo.orchestration._builder import Orchestration
from perceivo.orchestration._builder._project_io import load
from perceivo.runner.executor import run_orchestration

LIBRARY_START = "a0000000-0000-000a-00c0-000000000011"
LIBRARY_END = "a0000000-0000-000a-00c0-000000000010"

_MANIFEST = """extension_type: AGENT
name: {tag}
input_ports:
- port_name: input
  port_type: in
output_ports:
- port_name: output
  port_type: out
"""


def _agent(tag: str) -> str:
    return f"""
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        data = dict(inp.request_data or {{}})
        data["path"] = list(data.get("path", [])) + ["{tag}"]
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="{tag}"),
                           response_data=data)
ENTRY_CLASS = "C"
"""


def _sources(root: Path, *tags: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for tag in tags:
        (root / f"{tag}.py").write_text(_agent(tag), encoding="utf-8")
        (root / f"{tag}.yaml").write_text(_MANIFEST.format(tag=tag), encoding="utf-8")
    return root


def _add(orch: Orchestration, tag: str) -> Any:
    return orch.add_node(tag, "AGENT", script=f"{tag}.py", manifest=f"{tag}.yaml")


def _chain(src: Path) -> Orchestration:
    parent = Orchestration("parent", project_dir=str(src))
    a, b, c = (_add(parent, t) for t in "abc")
    parent.add_edge(a, b)
    parent.add_edge(b, c)
    parent.add_library_endpoints()
    return parent


def _extract_b(parent: Orchestration, src: Path, mode: str) -> Any:
    child = Orchestration("inner", project_dir=str(src))
    _add(child, "b")
    child.add_library_endpoints()
    parent.remove_node("node_b")
    node = parent.add_orchestration_node(
        "inner", child, execution_mode=mode, mainflow_target=parent.nodes["node_c"]
    )
    parent.add_edge(parent.nodes["node_a"], node)
    return node


async def _run(project: Path, monkeypatch) -> Any:
    monkeypatch.chdir(project)
    return await run_orchestration(str(project), input_data={"q": "hi"})


def _stable(bundle: Any) -> Any:
    if isinstance(bundle, dict):
        return {k: _stable(v) for k, v in bundle.items() if k != "exported_at"}
    if isinstance(bundle, list):
        return [_stable(v) for v in bundle]
    return bundle


# --------------------------------------------------------------------------- #
# end to end                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["subprocess", "inline"])
async def test_extract_to_child_save_run_export_reimport(tmp_path, monkeypatch, mode):
    src = _sources(tmp_path / "src", "a", "b", "c")
    parent = _chain(src)
    _extract_b(parent, src, mode)

    out = tmp_path / "out"
    parent.save(str(out))
    saved = yaml.safe_load((out / "orchestration.yaml").read_text(encoding="utf-8"))
    assert "node_b" not in {n["id"] for n in saved["nodes"]}
    assert not [p for p in saved["ports"] if p["node_id"] == "node_b"]
    inner = next(n for n in saved["nodes"] if n["id"] == "node_inner")
    assert inner["project"] == "nested/inner"
    assert inner["config"] == {"execution_mode": mode, "mainflow_target_node_ref": "node_c"}
    assert "embedded" not in inner
    assert (out / "nested" / "inner" / "b.py").is_file()

    result = await _run(out, monkeypatch)
    assert result.success, result.error
    assert result.response_data["path"] == ["a", "b", "c"]

    first = orchestration_to_bundle(out)
    comps = first["components"].values()
    assert {c["template_source_id"] for c in comps if c["category"] in ("START", "END")} == {
        LIBRARY_START,
        LIBRARY_END,
    }
    (child,) = first["orchestrations"].values()
    assert {c["category"] for c in child["components"].values()} == {"START", "AGENT", "END"}

    again = orch_api.load_bundle(first, str(tmp_path / "reimported"))
    second = orchestration_to_bundle(again)
    assert json.dumps(_stable(second), sort_keys=True) == json.dumps(_stable(first), sort_keys=True)
    rerun = await _run(Path(again), monkeypatch)
    assert rerun.success, rerun.error
    assert rerun.response_data["path"] == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# remove_node / remove_edge                                                    #
# --------------------------------------------------------------------------- #


def test_remove_node_drops_edges_ports_and_config_hops(tmp_path):
    parent = _chain(_sources(tmp_path, "a", "b", "c"))
    parent.nodes["node_a"].config["mainflow_target_node_ref"] = "node_b"
    removed = parent.remove_node(parent.nodes["node_b"])

    assert removed.id == "node_b"
    assert "node_b" not in parent.nodes
    assert "node_b" not in parent._port_registry.values()
    assert all("node_b" not in e.source_port_id + e.target_port_id for e in parent.edges)
    assert "mainflow_target_node_ref" not in parent.nodes["node_a"].config
    with pytest.raises(KeyError, match="node_b"):
        parent.remove_node("node_b")


def test_remove_edge_by_identity_and_by_filter(tmp_path):
    parent = _chain(_sources(tmp_path, "a", "b", "c"))
    a, b, c = (parent.nodes[f"node_{t}"] for t in "abc")
    edge = next(e for e in parent.edges if e.source_port_id == a.port_id("output"))
    assert parent.remove_edge(edge) == [edge]
    assert edge not in parent.edges

    removed = parent.remove_edge(source=b, target="node_c", source_port="output", kind="mainflow")
    assert len(removed) == 1
    assert removed[0].target_port_id == c.port_id("input")
    assert parent.remove_edge(target_port="input", target=parent.nodes["node_end"])

    with pytest.raises(ValueError, match="no matching edge"):
        parent.remove_edge(source=a, target=c)
    with pytest.raises(TypeError, match="not both/neither"):
        parent.remove_edge()
    with pytest.raises(TypeError, match="not both/neither"):
        parent.remove_edge(edge, source=a)


# --------------------------------------------------------------------------- #
# add_orchestration_node                                                       #
# --------------------------------------------------------------------------- #


def test_embed_a_child_directory_and_reject_bad_input(tmp_path):
    src = _sources(tmp_path / "src", "a", "b")
    child_dir = tmp_path / "child"
    child = Orchestration("child", project_dir=str(src))
    _add(child, "b")
    child.add_library_endpoints()
    child.save(str(child_dir))

    parent = Orchestration("parent", project_dir=str(src))
    first = parent.add_orchestration_node("A very long child name, with punctuation!", child_dir)
    second = parent.add_orchestration_node("x", str(child_dir), dirname=Path(first.project).name)
    assert first.project == "nested/A_very_long_child_name_w"
    assert second.project == first.project + "_2"
    assert first.config == {"execution_mode": "subprocess"}

    out = tmp_path / "out"
    parent.save(str(out))
    for node in (first, second):
        assert (out / node.project / "orchestration.yaml").is_file()
        assert (out / node.project / "b.py").is_file()
    # A loaded parent carries the children by path, as before.
    reloaded = load(str(out))
    reloaded.save(str(tmp_path / "copy"))
    assert (tmp_path / "copy" / first.project / "b.py").is_file()

    with pytest.raises(ValueError, match="execution_mode"):
        parent.add_orchestration_node("m", child_dir, execution_mode="thread")
    with pytest.raises(FileNotFoundError, match=r"orchestration\.yaml"):
        parent.add_orchestration_node("m", tmp_path)
    with pytest.raises(ValueError, match="one path segment"):
        parent.add_orchestration_node("m", child_dir, dirname="../up")


def test_embedded_child_is_not_serialized_and_escape_is_refused(tmp_path):
    parent = Orchestration("parent")
    node = parent.add_orchestration_node("inner", Orchestration("inner"))
    assert "embedded" not in parent.to_dict()["nodes"][0]
    node.project = "../escape"
    with pytest.raises(ValueError, match="escapes"):
        parent.save(str(tmp_path / "out"))


# --------------------------------------------------------------------------- #
# add_library_endpoints                                                        #
# --------------------------------------------------------------------------- #


def test_library_endpoints_reuse_existing_and_hop_an_orchestration_leaf(tmp_path):
    orch = Orchestration("o", project_dir=str(_sources(tmp_path, "a")))
    a = _add(orch, "a")
    inner = orch.add_orchestration_node("inner", Orchestration("inner"))
    orch.add_edge(a, inner)
    start, end = orch.add_library_endpoints()

    assert [p["name"] for p in start.ports] == ["external_input", "output"]
    assert [p["name"] for p in end.ports] == ["input", "external_output"]
    assert inner.config["mainflow_target_node_ref"] == end.id
    assert len(orch.edges) == 2  # start -> a, a -> inner

    assert orch.add_library_endpoints() == (start, end)
    assert len(orch.edges) == 2
    bare = Orchestration("bare")
    bare.add_library_endpoints(connect=False)
    assert bare.edges == []
