"""The local runner matches the platform where seed assets depend on it.

Each section pins one behaviour a seed asset needs to run locally the way it
runs on the platform: one embedding vector per input text, the capabilities
reads, Milvus collections seeded before the run, the packages a component's
manifest declares, a warning for an invoke slot the graph never lists, ordered
log lines, and a feature component's ingest files. An inference component's
model bundle is pinned in ``test_local_runner_model_bundle.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext
from perceivo.config import Config, MocksConfig
from perceivo.local.context import LocalExtensionManager
from perceivo.local.mocks import http_intercept
from perceivo.local.mocks.registry import MockRegistry
from perceivo.orchestration._builder._bundle_integrity import collect_additional_packages
from perceivo.runner import run_component
from perceivo.types.errors import ConfigurationError, NotFoundError
from perceivo.validator._orchestration._main import validate_graph

# --- embeddings: one vector per input text -----------------------------------

EMBED_URL = "https://api.openai.com/v1/embeddings"


async def _embed(**request: Any) -> dict[str, Any]:
    reg = MockRegistry(Config())
    reg.install_http()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(EMBED_URL, **request)
        return response.json()  # type: ignore[no-any-return]
    finally:
        reg.uninstall_http()
        assert http_intercept._refcount == 0


async def test_embedding_mock_returns_one_vector_per_text() -> None:
    body = await _embed(json={"model": "m", "input": ["a", "b", "c"]})
    assert [row["index"] for row in body["data"]] == [0, 1, 2]
    assert len({tuple(row["embedding"]) for row in body["data"]}) == 3


async def test_embedding_mock_reads_a_raw_json_body_and_dimensions() -> None:
    raw = json.dumps({"model": "m", "input": ["a", "b"], "dimensions": 5}).encode()
    body = await _embed(content=raw, headers={"content-type": "application/json"})
    assert len(body["data"]) == 2
    assert all(len(row["embedding"]) == 5 for row in body["data"])


async def test_embedding_mock_treats_a_string_as_one_text() -> None:
    body = await _embed(json={"model": "m", "input": "only one"})
    assert len(body["data"]) == 1


async def test_embedding_mock_treats_a_token_list_as_one_text() -> None:
    body = await _embed(json={"model": "m", "input": [1, 2, 3]})
    assert len(body["data"]) == 1


async def test_embedding_vectors_are_deterministic_per_text() -> None:
    first = await _embed(json={"input": ["x", "y"]})
    second = await _embed(json={"input": ["y"]})
    assert first["data"][1]["embedding"] == second["data"][0]["embedding"]


# --- capabilities ---------------------------------------------------------------

CAP_SEED = {
    "capabilities": {
        "items": [
            {"id": "cap-b", "name": "Bravo"},
            {"id": "cap-a", "name": "Alpha", "organization_id": "org-1"},
        ],
        "graphs": {
            "cap-a": [
                {
                    "graph_id": "g-1",
                    "entries": [{"orchestration_id": "o-1"}],
                    "summary": {"runs": 1},
                }
            ]
        },
    }
}


def _ext(seed: dict[str, Any] | None = None, **modes: str) -> LocalExtensionManager:
    return LocalExtensionManager(Config(mocks=MocksConfig(seed=seed or {}, modes=dict(modes))))


async def test_local_manager_serves_seeded_capabilities() -> None:
    ext = _ext(CAP_SEED)
    caps = await ext.list_capabilities()
    assert [c["name"] for c in caps] == ["Alpha", "Bravo"]
    assert caps[1]["organization_id"] == "mock-organization"
    refs = await ext.list_execution_graphs("cap-a")
    assert [r["graph_id"] for r in refs] == ["g-1"]
    graph = await ext.get_execution_graph(graph_id="g-1", capability_id="cap-a")
    assert graph is not None and graph["entries"] == [{"orchestration_id": "o-1"}]
    assert await ext.get_execution_graph(graph_id="nope", capability_id="cap-a") is None
    assert await ext.list_execution_graphs("cap-b") == []


async def test_public_capabilities_client_runs_against_the_local_manager() -> None:
    _ExecutionContext.set_ext(_ext(CAP_SEED))
    try:
        caps = await perceivo.capabilities.list()
        assert [c.id for c in caps] == ["cap-a", "cap-b"]
        refs = await perceivo.capabilities.graphs("cap-a")
        assert refs[0].blob_path.endswith("g-1.json")
        graph = await perceivo.capabilities.graph("g-1", capability_id="cap-a")
        assert graph.graph_type == "full_run"
        assert graph.summary == {"runs": 1}
        with pytest.raises(NotFoundError):
            await perceivo.capabilities.graph("missing", capability_id="cap-a")
    finally:
        _ExecutionContext.clear()


async def test_unseeded_capabilities_are_empty() -> None:
    ext = _ext()
    assert await ext.list_capabilities() == []
    assert await ext.list_execution_graphs("anything") == []


async def test_disabled_capabilities_raise_a_configuration_error() -> None:
    ext = _ext(capabilities="disabled")
    with pytest.raises(ConfigurationError, match="capabilities"):
        await ext.list_capabilities()


# --- Milvus: seeded collections ---------------------------------------------------


async def test_milvus_collections_seed_as_a_list_of_names() -> None:
    ext = _ext({"milvus": {"collections": ["general_knowledge", "sysops_knowledge"]}})
    milvus = await ext.get_milvus()
    assert await milvus.list_collections() == ["general_knowledge", "sysops_knowledge"]
    assert await milvus.has_collection("general_knowledge")


async def test_milvus_collections_seed_as_documents() -> None:
    seed = {
        "milvus": {
            "collections": {
                "docs": [{"text": "hello", "vector": [1.0, 0.0], "metadata": {"k": 1}}],
            }
        }
    }
    milvus = await _ext(seed).get_milvus()
    assert await milvus.list_collections() == ["docs"]
    assert milvus._collections["docs"][0]["id"] == "docs-0"
    assert milvus._collections["docs"][0]["text"] == "hello"


async def test_milvus_defaults_to_no_collections() -> None:
    milvus = await _ext().get_milvus()
    assert await milvus.list_collections() == []


# --- additional packages declared in the component manifest ----------------------


def test_packages_declared_in_manifest_yaml_are_collected() -> None:
    data = {
        "orchestration": {"additional_packages": None},
        "components": {
            "a": {
                "additional_packages": None,
                "manifest_yaml": "additional_packages:\n  - anthropic\n",
            },
            "b": {"additional_packages": ["httpx"], "manifest_yaml": "name: b\n"},
            "c": {"manifest_yaml": ": not yaml : ["},
        },
    }
    assert collect_additional_packages(data) == ["anthropic", "httpx"]


# --- validator: an invoke slot the graph never lists -------------------------------


def _slot(key: str, port_type: str) -> dict[str, str]:
    return {"key": key, "port_type": port_type, "direction": "OUT"}


def _row(port_id: str, node_id: str, name: str, port_type: str, direction: str) -> dict[str, str]:
    return {
        "id": port_id,
        "node_id": node_id,
        "name": name,
        "port_type": port_type,
        "direction": direction,
    }


def _router(listed: tuple[str, ...]) -> dict[str, Any]:
    nodes = [
        {"id": "start", "component_type": "START"},
        {"id": "end", "component_type": "END"},
        {
            "id": "fsm",
            "name": "Router",
            "component_type": "FSM",
            "component_ports": [
                {"key": "input", "port_type": "in", "direction": "IN"},
                _slot("branch_1", "state"),
                _slot("branch_2", "state"),
                _slot("policy", "policy"),
            ],
        },
    ]
    ports = [
        _row("s_out", "start", "output", "out", "out"),
        _row("f_in", "fsm", "input", "in", "in"),
        _row("e_in", "end", "input", "in", "in"),
    ]
    for key in listed:
        ports.append(_row(f"f_{key}", "fsm", key, "policy" if key == "policy" else "state", "out"))
    edges = [{"source_port_id": "s_out", "target_port_id": "f_in", "kind": "MAINFLOW"}]
    return {"nodes": nodes, "ports": ports, "edges": edges}


def _slot_checks(graph: dict[str, Any]) -> list[Any]:
    report = validate_graph(**graph)
    return [c for c in report.checks if c.id.startswith("ORCH-UNLISTED-SLOT")]


def test_a_declared_slot_missing_from_the_graph_warns() -> None:
    checks = _slot_checks(_router(("branch_1",)))
    assert [c.severity for c in checks] == ["warning", "warning"]
    messages = " ".join(c.message for c in checks)
    assert "'branch_2'" in messages
    assert "'policy'" in messages
    assert "'branch_1'" not in messages


def test_listed_slots_do_not_warn() -> None:
    assert _slot_checks(_router(("branch_1", "branch_2", "policy"))) == []


# --- log lines: written in order, all present when the run returns -----------------

_LOG_START = """
import perceivo

class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        perceivo.log.info("supervisor: starting the line")
        return StartOutput(artifact=None, response_data={})
ENTRY_CLASS = "StartC"
"""

_LOG_AGENT = """
import perceivo

class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput
        perceivo.log.info("cut: working")
        return AgentOutput(artifact=None, response_data={})
ENTRY_CLASS = "AgentC"
"""

_LOG_END = """
import perceivo

class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        perceivo.log.info("supervisor: line finished")
        return EndOutput(artifact=None, response_data={"done": True})
ENTRY_CLASS = "EndC"
"""


def _logging_orchestration(workspace: Path) -> Path:
    from perceivo.orchestration._builder import Orchestration

    orch = Orchestration("logorder")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml", node_id="s")
    a = orch.add_node("cut", "AGENT", script="a.py", manifest="a.yaml", node_id="a")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml", node_id="e")
    orch.add_edge(s, a)
    orch.add_edge(a, e)
    orch.save(str(workspace))
    for name, body in (("s", _LOG_START), ("a", _LOG_AGENT), ("e", _LOG_END)):
        (workspace / f"{name}.py").write_text(body, encoding="utf-8")
        (workspace / f"{name}.yaml").write_text(
            "ports: []\ninput_parameters: []\nrequired_secrets: []\n", encoding="utf-8"
        )
    return workspace


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


async def test_node_log_lines_print_in_run_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from perceivo.runner.executor import run_orchestration

    monkeypatch.chdir(tmp_path)
    _logging_orchestration(tmp_path)
    caplog.set_level("INFO", logger="perceivo")
    result = await run_orchestration(str(tmp_path))
    lines = _messages(caplog)
    assert result.success, result.error
    wanted = ["supervisor: starting the line", "Executing: cut (AGENT)", "cut: working"]
    wanted.append("supervisor: line finished")
    assert all(line in lines for line in wanted), lines
    assert [lines.index(line) for line in wanted] == sorted(lines.index(line) for line in wanted)


async def test_a_component_log_line_is_written_before_the_run_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    script = tmp_path / "e.py"
    script.write_text(_LOG_END, encoding="utf-8")
    caplog.set_level("INFO", logger="perceivo")
    result = await run_component(str(script), component_type="END")
    assert result.success, result.error
    assert "supervisor: line finished" in _messages(caplog)


# --- feature: the ingest folder's files arrive as bytes ------------------------------

_FEATURE = """
import perceivo
from perceivo.intelligence import FeatureComponent
from perceivo.types import BatchResult, IngestPlan

SEEN = {}

class Feed(FeatureComponent):
    def get_schema(self):
        return {}

    async def get_samples(self, *args, **kwargs):
        return []

    async def prepare_data(self, input):
        contents = input.source_config.get("file_contents") or {}
        SEEN.update({name: type(data).__name__ for name, data in contents.items()})
        rows = sum(data.count(10) for data in contents.values())
        return IngestPlan(total_items=rows, batch_size=rows or 1)

    async def process_batch(self, index):
        return BatchResult(records={}, records_processed=len(SEEN))

ENTRY_CLASS = "Feed"
"""


def _feature(tmp_path: Path) -> tuple[str, str]:
    script = tmp_path / "feed.py"
    script.write_text(_FEATURE, encoding="utf-8")
    org = tmp_path / "org"
    config = tmp_path / "perceivo.yaml"
    config.write_text(f"files:\n  org_dir: {org.as_posix()}\n", encoding="utf-8")
    return str(script), str(config)


async def test_feature_ingest_folder_files_are_preloaded_as_bytes(tmp_path: Path) -> None:
    script, config = _feature(tmp_path)
    folder = tmp_path / "org" / "recsys"
    folder.mkdir(parents=True)
    (folder / "users.csv").write_bytes(b"user_id\nu1\nu2\n")
    (folder / "items.csv").write_bytes(b"item_id\ni1\n")
    (folder / "nested").mkdir()
    result = await run_component(
        script,
        component_type="FEATURE",
        input_data={"ingest": {"source_path": "recsys"}},
        config_path=config,
    )
    assert result.success, result.error
    assert result.response_data["records_processed"] == 2
    assert result.response_data["batches"] == 1


async def test_feature_inline_file_contents_are_handed_over_as_bytes(tmp_path: Path) -> None:
    script, config = _feature(tmp_path)
    contents = {"a.csv": "x\n1\n", "b.csv": "y\n"}
    result = await run_component(
        script,
        component_type="FEATURE",
        input_data={"ingest": {"source_config": {"file_contents": contents}}},
        config_path=config,
    )
    assert result.success, result.error
    assert result.response_data["records_processed"] == 2
