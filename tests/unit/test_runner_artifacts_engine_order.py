"""Where the engine puts an artifact: an invoke's "main", START's seed, END's
"main" and a script-less REVIEW's pass-through."""

from __future__ import annotations

import yaml

from perceivo.runner import run_orchestration

from .test_runner_artifacts_forwarded import (
    _ARTIFACT_READER,
    _END,
    _PRODUCER,
    _READER,
    _START,
    UTF8,
    _run,
    _write,
)

_MAIN_ONLY_CALLER = """
import perceivo

class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact
        seen = await perceivo.flow.invoke("p", {}, None, {"main": Artifact(type="text", data="X")})
        data = dict(inp.request_data or {})
        data["invoked_artifact"] = seen["artifact_saw"]
        return AgentOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""


async def test_an_invoked_node_is_given_the_main_of_the_artifacts_it_is_passed(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            # Leaves the run holding doc_a and doc_b, and no "main".
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("call", "AGENT", _MAIN_ONLY_CALLER),
            ("t", "TOOL", _ARTIFACT_READER),
            ("e", "END", _END),
        ],
        invokes=[("call", "t")],
    )
    data = await _run(root)
    assert data["invoked_artifact"] == "X"


# Records the artifact names START was given.
_START_ARTIFACTS = """
class C:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=inp.artifact, response_data={"start_saw": sorted(inp.artifacts)})
ENTRY_CLASS = "C"
"""


async def test_a_run_given_an_artifact_gives_start_it_as_main(tmp_path):
    from perceivo.primitives import Artifact

    root = _write(tmp_path / "p", [("s", "START", _START_ARTIFACTS), ("e", "END", _END)])
    result = await run_orchestration(
        str(root), input_data={}, input_artifact=Artifact(type="text", data="in")
    )
    assert result.success, result.error
    assert result.response_data["start_saw"] == ["main"]


# Returns an artifact beside a named artifact of its own.
_END_WITH_BOTH = """
class C:
    async def end(self, inp):
        from perceivo.primitives import Artifact, EndOutput
        return EndOutput(
            artifact=Artifact(type="text", data="out"),
            response_data={},
            artifacts={"x": Artifact(type="text", data="x")},
        )
ENTRY_CLASS = "C"
"""


async def test_a_run_holds_ends_artifact_as_main_beside_its_named_ones(tmp_path):
    root = _write(tmp_path / "p", [("s", "START", _START), ("e", "END", _END_WITH_BOTH)])
    data = await _run(root)
    assert data["run_artifacts"] == ["main", "x"]


async def test_a_review_node_without_a_script_passes_the_runs_artifacts_on(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("review", "REVIEW", ""),
            ("read", "TOOL", _READER.format(output="ToolOutput", key="read_saw", extra="")),
            ("e", "END", _END),
        ],
    )
    graph = yaml.safe_load((root / "orchestration.yaml").read_text(encoding=UTF8))
    for node in graph["nodes"]:
        if node["id"] == "review":
            del node["script"]
    (root / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding=UTF8)
    (root / "review.py").unlink()
    data = await _run(root)
    assert data["read_saw"] == ["doc_a", "doc_b"]
