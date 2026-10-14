"""The notebook quickstart's graph ends holding the END's artifact as ``"main"``.

The quickstart builds START -> AGENT -> END from the scaffolder's START and END.
The scaffolded END returns the artifact it is given and no named artifacts, so the
run's terminal ``artifacts`` collection is that artifact under ``"main"``.
"""

from __future__ import annotations

from perceivo import component
from perceivo.orchestration import Orchestration
from perceivo.runner import run_orchestration

_AGENT = """
from perceivo.primitives import AgentOutput, Artifact, ArtifactType


class Summarize:
    async def run(self, inp):
        summary = "summary of " + str((inp.request_data or {}).get("query", ""))
        return AgentOutput(
            artifact=Artifact(type=ArtifactType.TEXT, data=summary, mime_type="text/plain"),
            response_data={"result": summary, "conversation": [summary]},
        )


ENTRY_CLASS = "Summarize"
"""


async def test_the_quickstart_graph_ends_holding_the_end_artifact_as_main(tmp_path, monkeypatch):
    project = tmp_path / "quickstart"
    project.mkdir()
    for name, component_type in (("start", "START"), ("summarize", "AGENT"), ("end", "END")):
        component.create(name, component_type, str(project))
    (project / "summarize.py").write_text(_AGENT, encoding="utf-8")

    graph = Orchestration("document-summarizer", description="Offline SDK quickstart")
    start = graph.add_node(
        "start", "START", script="start.py", manifest="start.yaml", node_id="start"
    )
    summarize = graph.add_node(
        "summarize", "AGENT", script="summarize.py", manifest="summarize.yaml", node_id="summarize"
    )
    end = graph.add_node("end", "END", script="end.py", manifest="end.yaml", node_id="end")
    graph.add_edge(start, summarize)
    graph.add_edge(summarize, end)
    graph.save(str(project))

    monkeypatch.chdir(project)
    result = await run_orchestration(str(project), input_data={"query": "doc"})

    assert result.success, result.error
    assert result.response_data == {"result": "summary of doc"}
    assert list(result.artifacts) == ["main"]
    assert result.artifacts["main"].data == result.artifact.data == "summary of doc"
    assert result.conversation_log is None
