"""Every node's input carries a copy of the run's named artifacts, and each node's
result replaces them, as on the platform."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
import yaml

from perceivo.runner import run_component, run_orchestration

from .test_component_kinds_run_locally import FEATURE

UTF8 = "utf-8"

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
        data = dict(inp.response_data or {})
        data["end_saw"] = sorted(inp.artifacts)
        return EndOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""

# Emits two named artifacts, as a Files directory read does.
_PRODUCER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import Artifact, {output}
        docs = {{n: Artifact(type="text", data=n) for n in ("doc_a", "doc_b")}}
        return {output}(artifact=docs["doc_a"], response_data={{}}, artifacts=docs)
ENTRY_CLASS = "C"
"""

# Records the artifact names it was given, then changes its own copy.
_READER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import Artifact, {output}
        data = dict(inp.request_data or {{}})
        data["{key}"] = sorted(inp.artifacts)
        inp.artifacts["leaked"] = Artifact(type="text", data="x")
        inp.artifacts.pop("doc_b", None)
        return {output}(artifact=inp.artifact, response_data=data{extra})
ENTRY_CLASS = "C"
"""

# Invokes port "p" with named artifacts of its own, then with none.
_CALLER = """
import perceivo

class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact
        own = {"mine": Artifact(type="text", data="m")}
        given = await perceivo.flow.invoke("p", {}, None, own)
        bare = await perceivo.flow.invoke("p", {}, None)
        data = dict(inp.request_data or {})
        data["given"] = given["seen"]
        data["bare"] = bare["seen"]
        return AgentOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""

# Invokes port "p" in parallel with no artifact of its own.
_PARALLEL_CALLER = """
import perceivo

class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        out = await perceivo.flow.invoke_parallel(["p"], {})
        data = dict(inp.request_data or {})
        data["parallel"] = out["p"]["response_data"]["seen"]
        return AgentOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""

# Emits a "main" artifact beside another, so the run holds both.
_MAIN_PRODUCER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact
        docs = {n: Artifact(type="text", data=n) for n in ("main", "doc_a")}
        return AgentOutput(artifact=docs["main"], response_data={}, artifacts=docs)
ENTRY_CLASS = "C"
"""

# Changes its own input artifacts, then invokes port "p" with none of its own,
# so the invoked node is given the run's.
_MUTATING_CALLER = """
import perceivo

class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact
        inp.artifacts["leaked"] = Artifact(type="text", data="x")
        inp.artifacts.pop("doc_b", None)
        seen = await perceivo.flow.invoke("p", {}, None)
        data = dict(inp.request_data or {})
        data["invoked_saw"] = seen["seen"]
        return AgentOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""

# Returns {returns} after a two-artifact upstream.
_RETURNS = """
class C:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        return AgentOutput(response_data=dict(inp.request_data or {{}}), {returns})
ENTRY_CLASS = "C"
"""

_REVIEW = """
class C:
    async def run(self, inp):
        return None
ENTRY_CLASS = "C"
"""

_TARGET = """
class C:
    async def run(self, inp):
        from perceivo.primitives import ToolOutput
        return ToolOutput(response_data={"seen": sorted(inp.artifacts)})
ENTRY_CLASS = "C"
"""


def _write(root: Path, nodes: list[tuple[str, str, str]], invokes=()) -> Path:
    """``nodes`` is the mainflow, as (id, component type, script); ``invokes``
    is (caller id, target id) pairs wired through a tool port named ``p``."""
    root.mkdir(parents=True, exist_ok=True)
    graph: dict = {"nodes": [], "ports": [], "edges": []}
    for node_id, ctype, script in nodes:
        (root / f"{node_id}.py").write_text(script, encoding=UTF8)
        graph["nodes"].append(
            {"id": node_id, "name": node_id, "component_type": ctype, "script": f"{node_id}.py"}
        )
        for name, kind in (("in", "in"), ("out", "out")):
            graph["ports"].append(
                {"id": f"{node_id}_{name}", "node_id": node_id, "name": name, "port_type": kind}
            )
    mainflow = [n for n, _, _ in nodes if n not in {t for _, t in invokes}]
    for a, b in pairwise(mainflow):
        graph["edges"].append(
            {"source_port_id": f"{a}_out", "target_port_id": f"{b}_in", "kind": "MAINFLOW"}
        )
    for caller, target in invokes:
        graph["ports"].append(
            {"id": f"{caller}_p", "node_id": caller, "name": "p", "port_type": "tool"}
        )
        graph["edges"].append(
            {"source_port_id": f"{caller}_p", "target_port_id": f"{target}_in", "kind": "INVOKE"}
        )
    (root / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding=UTF8)
    return root


async def _run(root: Path) -> dict:
    result = await run_orchestration(str(root), input_data={})
    assert result.success is True, result.error
    return {**result.response_data, "run_artifacts": sorted(result.artifacts)}


@pytest.mark.parametrize(
    ("producer", "output"),
    [("AGENT", "AgentOutput"), ("TOOL", "ToolOutput")],
)
@pytest.mark.parametrize(
    ("reader", "reader_output", "extra"),
    [
        ("AGENT", "AgentOutput", ""),
        ("TOOL", "ToolOutput", ""),
        ("FSM", "FsmOutput", ""),
        ("POLICY", "PolicyOutput", ', next_state="done"'),
        ("ADAPTER", "AdapterOutput", ""),
    ],
)
async def test_both_upstream_artifacts_reach_the_downstream_input(
    tmp_path, producer, output, reader, reader_output, extra
):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", producer, _PRODUCER.format(output=output)),
            ("read", reader, _READER.format(output=reader_output, key="read_saw", extra=extra)),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["read_saw"] == ["doc_a", "doc_b"]
    # The reader returns only an artifact, which the run then holds as "main".
    assert data["end_saw"] == ["main"]


async def test_a_component_changing_its_input_artifacts_leaves_the_runs_unchanged(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("call", "AGENT", _MUTATING_CALLER),
            ("t", "TOOL", _TARGET),
            ("e", "END", _END),
        ],
        invokes=[("call", "t")],
    )
    data = await _run(root)
    assert data["invoked_saw"] == ["doc_a", "doc_b"]


async def test_an_invoked_node_gets_the_callers_artifacts_or_else_the_runs(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("call", "AGENT", _CALLER),
            ("t", "TOOL", _TARGET),
            ("e", "END", _END),
        ],
        invokes=[("call", "t")],
    )
    data = await _run(root)
    assert data["given"] == ["mine"]
    # The first invoke left the run holding what its target returned, no artifact,
    # so the second is given the run's artifacts, which are none.
    assert data["bare"] == []


async def test_a_parallel_invoke_without_an_artifact_gets_the_runs_main_only(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _MAIN_PRODUCER),
            ("call", "AGENT", _PARALLEL_CALLER),
            ("t", "TOOL", _TARGET),
            ("e", "END", _END),
        ],
        invokes=[("call", "t")],
    )
    data = await _run(root)
    assert data["parallel"] == ["main"]


@pytest.mark.parametrize(
    ("returns", "downstream_saw"),
    [
        # The named artifacts replace the run's, so the dropped doc_b is gone.
        ('artifact=inp.artifacts["doc_a"], artifacts={"doc_a": inp.artifacts["doc_a"]}', ["doc_a"]),
        # No named artifacts: the run holds the artifact alone, as "main".
        ('artifact=inp.artifacts["doc_a"]', ["main"]),
        # No artifact at all: the run holds none.
        ("artifact=None", []),
    ],
)
async def test_a_nodes_result_replaces_the_runs_artifacts(tmp_path, returns, downstream_saw):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("drop", "AGENT", _RETURNS.format(returns=returns)),
            ("read", "TOOL", _READER.format(output="ToolOutput", key="read_saw", extra="")),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["read_saw"] == downstream_saw


async def test_a_review_node_leaves_the_runs_artifacts_as_they_were(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("review", "REVIEW", _REVIEW),
            ("read", "TOOL", _READER.format(output="ToolOutput", key="read_saw", extra="")),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["read_saw"] == ["doc_a", "doc_b"]


async def test_the_runs_artifacts_are_what_end_leaves(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["end_saw"] == ["doc_a", "doc_b"]
    # END returns only an artifact, so the run ends holding it as "main".
    assert data["run_artifacts"] == ["main"]


# Records the artifact it was given: its data, or None when it was given none.
_ARTIFACT_READER = """
class C:
    async def run(self, inp):
        from perceivo.primitives import ToolOutput
        data = dict(inp.request_data or {})
        data["artifact_saw"] = None if inp.artifact is None else inp.artifact.data
        return ToolOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""


async def test_a_node_returning_no_artifact_clears_the_one_carried_on(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("drop", "AGENT", _RETURNS.format(returns="artifact=None")),
            ("read", "TOOL", _ARTIFACT_READER),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["artifact_saw"] is None


# A review that returns an artifact of its own.
_REVIEW_REPLACING = """
class C:
    async def run(self, inp):
        from perceivo.primitives import Artifact, ReviewOutput
        return ReviewOutput(artifact=Artifact(type="text", data="reviewed"))
ENTRY_CLASS = "C"
"""


async def test_a_review_node_passes_on_the_artifact_it_was_given(tmp_path):
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("review", "REVIEW", _REVIEW_REPLACING),
            ("read", "TOOL", _ARTIFACT_READER),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["artifact_saw"] == "doc_a"


async def test_a_component_kind_node_passes_the_runs_artifacts_through(tmp_path):
    """The platform has no node handler for the component kinds, so no engine result
    states what such a node leaves the run holding; locally it leaves the run's
    artifact and artifacts as they were."""
    root = _write(
        tmp_path / "p",
        [
            ("s", "START", _START),
            ("make", "AGENT", _PRODUCER.format(output="AgentOutput")),
            ("feat", "FEATURE", FEATURE),
            ("e", "END", _END),
        ],
    )
    data = await _run(root)
    assert data["end_saw"] == ["doc_a", "doc_b"]
    # END returns the artifact it was given, the producer's, which the run holds as main.
    assert data["run_artifacts"] == ["main"]


# Records whether START was given an artifact.
_START_RECORDER = """
class C:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        data = {"start_given_none": inp.artifact is None}
        return StartOutput(artifact=inp.artifact, response_data=data)
ENTRY_CLASS = "C"
"""


async def test_a_run_without_an_input_artifact_gives_start_none(tmp_path):
    root = _write(
        tmp_path / "p",
        [("s", "START", _START_RECORDER), ("read", "TOOL", _ARTIFACT_READER), ("e", "END", _END)],
    )
    data = await _run(root)
    assert data["start_given_none"] is True
    assert data["artifact_saw"] is None
    assert data["run_artifacts"] == []


async def test_a_component_run_alone_without_an_artifact_is_given_none(tmp_path):
    script = tmp_path / "read.py"
    script.write_text(_ARTIFACT_READER.replace("ToolOutput", "AgentOutput"), encoding=UTF8)
    result = await run_component(str(script), component_type="AGENT")
    assert result.success, result.error
    # A real run hands the component None, so a run of it alone does too.
    assert result.response_data == {"artifact_saw": None}
    assert result.artifact is None
