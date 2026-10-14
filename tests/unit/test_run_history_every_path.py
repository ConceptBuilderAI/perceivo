"""Every run path writes exactly one history record, under the home ``history()`` reads."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

import perceivo
from perceivo._cli import main as cli
from perceivo.orchestration._builder._project_io import load as load_project
from perceivo.runner import run_component, run_orchestration, run_stream
from perceivo.runner.history import RunHistory
from perceivo.types.errors import ConfigurationError

UTF8 = "utf-8"
TOOL_SCRIPT = "tool.py"
ORCHESTRATION = "orchestration"
MISSING_NODE = "no_such_node"
GRAPH_FILE = "orchestration.yaml"
RUN_ID = "run_id"
COMPONENT = "component"

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

_TOOL = """
class C:
    async def run(self, inp):
        from perceivo.primitives import ToolOutput
        return ToolOutput(artifact=inp.artifact, response_data={"ok": True})
ENTRY_CLASS = "C"
"""


def _project(root: Path) -> Path:
    """A START -> END project with its own perceivo.yaml, so its home is root/.perceivo."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "perceivo.yaml").write_text("version: 1\n", encoding=UTF8)
    graph = {
        "name": "flow",
        "nodes": [
            {"id": "s", "name": "s", "component_type": "START", "script": "s.py"},
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
    (root / TOOL_SCRIPT).write_text(_TOOL, encoding=UTF8)
    return root


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("PERCEIVO_HOME", raising=False)
    monkeypatch.delenv("PERCEIVO_CONFIG", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return _project(tmp_path / "proj")


def _records(project: Path) -> list[dict[str, Any]]:
    runs = project / ".perceivo" / "runs"
    return [json.loads(p.read_text(encoding=UTF8)) for p in runs.glob("*.json")]


async def _drain(agen) -> None:
    async for _ in agen:
        pass


def _cli(*args: str) -> None:
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output


async def _via_run_orchestration(project: Path) -> None:
    await run_orchestration(str(project))


async def _via_orchestration_run(project: Path) -> None:
    await load_project(str(project)).run()


async def _via_cli_orchestration(project: Path) -> None:
    await asyncio.to_thread(_cli, "run", "orchestration", str(project))


async def _via_run_stream(project: Path) -> None:
    await _drain(run_stream(str(project)))


async def _via_run_component(project: Path) -> None:
    await run_component(str(project / TOOL_SCRIPT), component_type="TOOL")


async def _via_cli_component(project: Path) -> None:
    await asyncio.to_thread(_cli, "run", "component", str(project / TOOL_SCRIPT), "--type", "TOOL")


@pytest.mark.parametrize(
    ("run", "kind"),
    [
        (_via_run_orchestration, ORCHESTRATION),
        (_via_orchestration_run, ORCHESTRATION),
        (_via_cli_orchestration, ORCHESTRATION),
        (_via_run_stream, ORCHESTRATION),
        (_via_run_component, COMPONENT),
        (_via_cli_component, COMPONENT),
    ],
)
async def test_each_run_path_writes_exactly_one_record(project, run, kind):
    await run(project)
    records = _records(project)
    assert [record["kind"] for record in records] == [kind]
    assert records[0]["success"] is True


async def test_another_cwd_reads_the_same_records(project, tmp_path, monkeypatch):
    await run_orchestration(str(project))
    monkeypatch.chdir(tmp_path)
    page = await perceivo.runner.history(base_dir=str(project / "nested-dir-that-need-not-exist"))
    assert [run["orchestration_name"] for run in page.data] == ["flow"]


async def test_history_of_a_project_that_never_ran_is_empty_and_creates_nothing(project):
    page = await perceivo.runner.history(base_dir=str(project))
    assert (page.data, page.total, page.has_next) == ([], 0, False)
    assert not (project / ".perceivo").exists()


async def _seed(project: Path, count: int) -> list[str]:
    runs = RunHistory(str(project))
    ids = []
    for index in range(count):
        record = runs.start_run(f"r{index}")
        record.started_at = 1_000.0 + index
        runs.complete_run(record)
        ids.append(record.run_id)
    return ids


async def test_history_pages_through_every_record_newest_first(project):
    ids = await _seed(project, 5)
    page = await perceivo.runner.history(limit=2, base_dir=str(project))
    assert page.total == 5
    flags = [page.has_next]
    while (page := await page.next()) is not None:
        flags.append(page.has_next)
    assert flags == [True, True, False]
    first = await perceivo.runner.history(limit=2, base_dir=str(project))
    everything = [run["run_id"] async for run in first.all()]
    assert everything == list(reversed(ids))


async def test_a_limit_above_the_count_has_no_next_page(project):
    await _seed(project, 3)
    page = await perceivo.runner.history(limit=10, base_dir=str(project))
    assert (len(page.data), page.has_next) == (3, False)


def _break(project: Path) -> None:
    """Point START's config at an undeclared node: the compiler raises ConfigurationError."""
    graph = yaml.safe_load((project / GRAPH_FILE).read_text(encoding=UTF8))
    graph["nodes"][0]["config"] = {"mainflow_target_node_ref": MISSING_NODE}
    (project / GRAPH_FILE).write_text(yaml.safe_dump(graph), encoding=UTF8)


async def test_a_run_that_raises_writes_one_failed_record(project):
    _break(project)
    target = str(project)
    with pytest.raises(ConfigurationError, match=MISSING_NODE):
        await run_orchestration(target)
    records = _records(project)
    assert [(record["success"], record["kind"]) for record in records] == [(False, ORCHESTRATION)]
    assert MISSING_NODE in records[0]["error"]


async def test_a_raising_stream_names_a_record_history_can_find(project):
    _break(project)
    events = [event async for event in run_stream(str(project))]
    kinds = [event.event_type.value for event in events]
    assert kinds == ["log", "error", "complete"]
    run_id = events[0].data[RUN_ID]
    assert events[-1].data[RUN_ID] == run_id
    record = RunHistory(str(project)).get_run(run_id)
    assert (record.success, len(_records(project))) == (False, 1)
    page = await perceivo.runner.history(base_dir=str(project))
    assert [run[RUN_ID] for run in page.data] == [run_id]


async def test_control_an_unbroken_stream_names_its_successful_record(project):
    events = [event async for event in run_stream(str(project))]
    run_id = events[0].data[RUN_ID]
    assert RunHistory(str(project)).get_run(run_id).success is True
