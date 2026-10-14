"""The node after a nested child is given the artifact the child hands up.

The library generic END returns the artifact it is given, so in both modes the
node after the child sees the artifact of the child's last body node.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perceivo.orchestration._builder import Orchestration
from perceivo.runner.executor import run_orchestration

_MANIFEST = """extension_type: AGENT
name: {tag}
input_ports:
- port_name: input
  port_type: in
output_ports:
- port_name: output
  port_type: out
"""

_AGENT = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        data = dict(inp.request_data or {{}})
        seen = inp.artifact.data if inp.artifact is not None else None
        data["seen_by_{tag}"] = seen
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="from-{tag}"),
                           response_data=data)
ENTRY_CLASS = "C"
"""


def _sources(root: Path, *tags: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for tag in tags:
        (root / f"{tag}.py").write_text(_AGENT.format(tag=tag), encoding="utf-8")
        (root / f"{tag}.yaml").write_text(_MANIFEST.format(tag=tag), encoding="utf-8")
    return root


def _add(orch: Orchestration, tag: str):
    return orch.add_node(tag, "AGENT", script=f"{tag}.py", manifest=f"{tag}.yaml")


@pytest.mark.parametrize("mode", ["subprocess", "inline"])
async def test_the_node_after_a_nested_child_sees_the_childs_artifact(tmp_path, monkeypatch, mode):
    src = _sources(tmp_path / "src", "a", "b", "c")
    child = Orchestration("inner", project_dir=str(src))
    _add(child, "b")
    child.add_library_endpoints()

    parent = Orchestration("parent", project_dir=str(src))
    a, c = _add(parent, "a"), _add(parent, "c")
    inner = parent.add_orchestration_node("inner", child, execution_mode=mode, mainflow_target=c)
    parent.add_edge(a, inner)
    parent.add_library_endpoints()

    out = tmp_path / "out"
    parent.save(str(out))
    monkeypatch.chdir(out)
    result = await run_orchestration(str(out), input_data={"q": "hi"})

    assert result.success, result.error
    assert result.response_data["seen_by_b"] == "from-a"
    assert result.response_data["seen_by_c"] == "from-b"
