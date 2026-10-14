"""Unit tests for the local mock system (perceivo.local.mocks).

Covers the per-service mode resolver, the shipped seed data + merge/scaffold, the
individual mock adapters (LLM, Milvus, Elasticsearch, MCP, database, files), the
registry, the httpx interception, the LocalExtensionManager routing, and the
`perceivo mock` CLI verbs.
"""

from __future__ import annotations

import json

import httpx
import pytest
import yaml
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli
from perceivo.config import Config, MocksConfig, load
from perceivo.local.context import LocalExtensionManager
from perceivo.local.mocks import (
    MockDatabase,
    MockElasticsearch,
    MockLLM,
    MockMCPClient,
    MockMilvus,
    MockRegistry,
    MockTrace,
    http_intercept,
    load_seed,
    scaffold_seed,
)

# ---------------------------------------------------------------------------
# Config / mode resolution
# ---------------------------------------------------------------------------


def test_default_config_mocks_everything():
    cfg = Config()
    for service in (
        "llm",
        "redis",
        "milvus",
        "elasticsearch",
        "mcp",
        "files",
        "secrets",
        "database",
        "chat",
    ):
        assert cfg.service_mode(service) == "mock"
        assert cfg.is_mocked(service)


def test_explicit_mode_override_wins():
    cfg = Config(mocks=MocksConfig(modes={"milvus": "local"}))
    assert cfg.service_mode("milvus") == "local"
    assert not cfg.is_mocked("milvus")
    assert cfg.is_mocked("redis")


def test_native_local_implies_local_or_byo_key():
    cfg = Config()
    cfg.redis.enabled = True
    cfg.ai_foundry.enabled = True
    assert cfg.service_mode("redis") == "local"
    assert cfg.service_mode("llm") == "byo-key"
    assert not cfg.is_mocked("redis")
    assert not cfg.is_mocked("llm")


def test_mocks_disabled_yields_disabled():
    cfg = Config(mocks=MocksConfig(enabled=False))
    assert cfg.service_mode("milvus") == "disabled"
    assert not cfg.is_mocked("milvus")


def test_load_parses_mocks(tmp_path):
    doc = {
        "mocks": {
            "enabled": True,
            "default_mode": "mock",
            "modes": {"redis": "local"},
            "seed": {"llm": {"completion": "hi"}},
        }
    }
    path = tmp_path / "perceivo.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = load(str(path))
    assert cfg.mocks.modes == {"redis": "local"}
    assert cfg.mocks.seed["llm"]["completion"] == "hi"
    assert cfg.service_mode("redis") == "local"


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


def test_load_seed_merges_inline_over_defaults():
    seed = load_seed(MocksConfig(seed={"llm": {"completion": "custom"}}))
    assert seed["llm"]["completion"] == "custom"
    # untouched defaults survive the merge
    assert seed["secrets"]["default"] == "mock-secret"


def test_load_seed_merges_seed_file(tmp_path):
    seed_file = tmp_path / "perceivo.mock.yaml"
    seed_file.write_text(
        yaml.safe_dump({"mocks": {"seed": {"mcp": {"call": {"result": "fromfile"}}}}}),
        encoding="utf-8",
    )
    seed = load_seed(MocksConfig(seed_file=str(seed_file)))
    assert seed["mcp"]["call"]["result"] == "fromfile"


def test_scaffold_seed_only_selected_services():
    scaffold = scaffold_seed(["llm", "milvus"])
    assert set(scaffold) == {"llm", "milvus"}


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


async def test_mock_llm_invoke_shape_and_variation():
    llm = MockLLM({"completion": "base"})
    r1 = await llm.invoke(model="gpt-4o", messages=[{"role": "user", "content": "a"}])
    r2 = await llm.invoke(model="claude", messages=[{"role": "user", "content": "a"}])
    assert r1["choices"][0]["message"]["content"].startswith("base")
    # different model -> distinct completion (multi-agent consensus signal)
    assert r1["choices"][0]["message"]["content"] != r2["choices"][0]["message"]["content"]


async def test_mock_llm_vary_disabled():
    llm = MockLLM({"completion": "base", "vary": False})
    r = await llm.invoke(model="gpt-4o", messages=[{"role": "user", "content": "a"}])
    assert r["choices"][0]["message"]["content"] == "base"


async def test_mock_llm_embed_deterministic():
    llm = MockLLM({"embedding_dim": 8})
    a = await llm.embed(texts=["x", "y"])
    b = await llm.embed(texts=["x", "y"])
    assert len(a["embeddings"]) == 2
    assert len(a["embeddings"][0]) == 8
    assert a == b


async def test_mock_milvus_roundtrip_and_seed_fallback():
    milvus = MockMilvus(
        {"search_hits": [{"id": "s", "distance": 0.1, "text": "seed", "metadata": {}}]}
    )
    await milvus.create_collection("c", dimension=8)
    await milvus.insert("c", vectors=[[0.1] * 8], metadata=[{"text": "doc", "metadata": {"k": 1}}])
    hits = await milvus.search("c", query_vectors=[[0.1] * 8], top_k=5)
    assert hits[0][0]["entity"]["text"] == "doc"
    # empty collection -> seed hits
    empty = await milvus.search("other", query_vectors=[[0.0] * 8])
    assert empty[0][0]["entity"]["text"] == "seed"
    assert "c" in await milvus.list_collections()


async def test_mock_elasticsearch_roundtrip_and_seed():
    es = MockElasticsearch({"search_hits": [{"id": "s", "score": 1.0, "text": "seed"}]})
    await es.create_index("idx")
    await es.bulk_index("idx", documents=[{"content": "hello"}], doc_ids=["d1"])
    res = await es.search("idx", query={}, size=10)
    assert res["hits"]["total"]["value"] == 1
    assert res["hits"]["hits"][0]["_source"]["content"] == "hello"
    seeded = await es.search("empty", query={})
    assert seeded["hits"]["hits"][0]["_source"]["text"] == "seed"


async def test_mock_database_stateful_roundtrip():
    db = MockDatabase({})
    await db.query(
        "db", "INSERT INTO products (name, price) VALUES ('Widget', 9.99)", read_only=False
    )
    res = await db.query("db", "SELECT * FROM products WHERE name = 'Widget'")
    assert res["columns"] == ["name", "price"]
    assert res["rows"] == [["Widget", 9.99]]


async def test_mock_database_write_that_is_not_an_insert_stores_nothing_but_reports_a_row():
    """The no-match arm of `_apply_write`, which nothing reached before.

    A write the INSERT pattern does not match is accepted, stores nothing, and
    still reports one affected row -- the mock reports a row for ANY write, and
    that claim lives at the call site rather than in a `return 1` the rule
    against invariant returns (python:S3516) could not tell apart from an
    unfinished branch. Pinning it here means the constant cannot drift silently.
    """
    db = MockDatabase({})
    res = await db.query("db", "UPDATE products SET price = 1 WHERE id = 1", read_only=False)
    assert res["row_count"] == 1
    assert res["rows"] == []
    assert res["columns"] == []
    # Nothing was stored: a following SELECT falls through to the seed default.
    seeded = await db.query("db", "SELECT * FROM products")
    assert seeded["rows"] == [[1, "mock-row"]]


async def test_mock_database_seed_fallback():
    db = MockDatabase({"rows": [{"id": 7, "name": "seeded"}]})
    res = await db.query("db", "SELECT * FROM unknown")
    assert res["rows"] == [[7, "seeded"]]


async def test_mock_mcp_default_and_override():
    mcp = MockMCPClient(
        {
            "tools": [{"name": "t"}],
            "call": {"result": "def"},
            "calls": {"special": {"result": "sp"}},
        }
    )
    assert (await mcp.list_tools())[0]["name"] == "t"
    assert (await mcp.call_tool("anything"))["result"] == "def"
    assert (await mcp.call_tool("special"))["result"] == "sp"


def test_mock_trace_records():
    trace = MockTrace()
    trace.record("llm", "invoke", "mock", "model=x")
    trace.record("llm", "embed", "mock")
    assert trace.summary_by_service() == {"llm": 2}
    assert trace.to_list()[0]["operation"] == "invoke"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_force_managed_provider():
    reg = MockRegistry(Config())
    assert reg.force_managed_provider("provider", "openai") == "managed"
    assert reg.force_managed_provider("provider", "managed") == "managed"
    assert reg.force_managed_provider("model", "openai") == "openai"


def test_registry_secret_and_seed():
    reg = MockRegistry(Config())
    assert reg.secret("anything") == "mock-secret"
    assert reg.secret("CONTENT_SAFETY_ENDPOINT") == "https://mock-content-safety.local"
    assert reg.is_mock("llm")


def test_registry_not_mock_disables_http_install():
    cfg = Config(mocks=MocksConfig(modes={"llm": "byo-key"}))
    reg = MockRegistry(cfg)
    reg.install_http()  # llm not mocked -> no-op, must not patch
    assert http_intercept._refcount == 0


# ---------------------------------------------------------------------------
# httpx interception
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,kind",
    [
        ("https://api.openai.com/v1/chat/completions", "openai_chat"),
        ("https://api.openai.com/v1/embeddings", "openai_embed"),
        ("https://api.anthropic.com/v1/messages", "anthropic"),
        ("https://generativelanguage.googleapis.com/x:generateContent", "google"),
        (
            "https://acme.cognitiveservices.azure.com/contentsafety/text:shieldPrompt",
            "content_safety",
        ),
        ("https://example.com/other", None),
    ],
)
def test_http_match(url, kind):
    assert http_intercept._match(url) == kind


async def test_http_intercept_serves_llm_hosts():
    reg = MockRegistry(Config())
    reg.install_http()
    try:
        async with httpx.AsyncClient() as client:
            chat = await client.post("https://api.openai.com/v1/chat/completions", json={})
            safety = await client.post(
                "https://acme.cognitiveservices.azure.com/contentsafety/text:shieldPrompt", json={}
            )
        assert chat.json()["choices"][0]["message"]["content"]
        assert safety.json()["userPromptAnalysis"]["attackDetected"] is False
    finally:
        reg.uninstall_http()
    assert http_intercept._refcount == 0


# ---------------------------------------------------------------------------
# LocalExtensionManager routing
# ---------------------------------------------------------------------------


async def test_ext_routes_to_mocks_by_default():
    ext = LocalExtensionManager(Config())
    milvus = await ext.get_milvus()
    es = await ext.get_elasticsearch()
    assert isinstance(milvus, MockMilvus)
    assert isinstance(es, MockElasticsearch)
    assert await ext.is_mcp_available() is True
    assert isinstance(await ext.get_mcp_client(), MockMCPClient)
    llm = await ext.invoke_ai_foundry(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert llm["choices"][0]["message"]["content"]


async def test_ext_routes_external_database_query_to_the_mock():
    """External queries use the same mock, tagged so a trace shows which path ran."""
    ext = LocalExtensionManager(Config())
    await ext.execute_external_database_query(
        "conn-1", "INSERT INTO products (name, price) VALUES ('Widget', 9.99)", read_only=False
    )
    res = await ext.execute_external_database_query(
        "conn-1", "SELECT * FROM products WHERE name = 'Widget'"
    )
    assert res["rows"] == [["Widget", 9.99]]
    operations = [c.operation for c in ext._mocks.trace.calls if c.service == "database"]
    assert operations == ["external_query", "external_query"]


async def test_ext_secret_mock_fallback_and_env_provider_force():
    ext = LocalExtensionManager(Config())
    # unbound secret -> mock default value
    assert await ext.fetch_secret_value("some_secret") == "mock-secret"
    # provider env is forced to managed under mock llm
    ext.set_node_env({"provider": "openai", "model": "gpt-4o"}, {})
    assert ext.get_node_env()["values"]["provider"] == "managed"


async def test_ext_chat_message_mocked():
    """The mock reports the platform's own success value, not a local-only one.

    A component that checks whether its message went out checks for
    ``"sent"``, so a mock returning
    anything else fails locally and passes in the cloud — the divergence mock
    mode exists to prevent.
    """
    ext = LocalExtensionManager(Config())
    res = await ext.send_chat_message(channel="slack", recipient="x", message="hello")
    assert res["status"] == "sent"
    assert res["delivery_id"]


async def test_seeded_chat_failure_carries_its_detail_through():
    """A seeded failure can reproduce a *specific* platform failure.

    The point of the knob is letting a component's failure branch
    be tested against the message it will really receive. Seeding only the fact
    of a failure gives every failure the same empty detail, so a branch that
    switches on ``channel_not_found`` vs ``rate_limited`` cannot be reached.
    """
    cfg = Config()
    cfg.mocks.seed = {"chat": {"status": "failed", "error": "channel_not_found"}}
    ext = LocalExtensionManager(cfg)
    res = await ext.send_chat_message(channel="slack", recipient="x", message="hi")
    assert res["status"] == "failed"
    assert res["error"] == "channel_not_found"


async def test_the_mock_call_record_does_not_capture_the_recipient():
    """``mock_calls`` is a produced artifact and the recipient is an org secret.

    For slack/telegram/teams the recipient is a channel id, chat
    id or webhook URL held as an org secret. Mocking chat while leaving secrets
    real -- "use my config but do not actually message anyone" -- is an ordinary
    combination, and this record is serialized into run records and pasted into
    bug reports.
    """
    ext = LocalExtensionManager(Config())
    secret_recipient = "C0FFEE-SECRET-CHANNEL"
    await ext.send_chat_message(channel="slack", recipient=secret_recipient, message="hi")
    # to_list() is the serialized form -- literally what a run record carries.
    rendered = json.dumps(ext._mocks.trace.to_list(), default=str)
    assert secret_recipient not in rendered
    assert "slack" in rendered  # the channel is not sensitive, and is worth having


async def test_ext_files_mock_seeds_and_reads(tmp_path):
    cfg = Config()
    cfg.files.org_dir = str(tmp_path / "org")
    ext = LocalExtensionManager(cfg)
    text = await ext.org_files.read_text("story_beginning.txt")
    assert "lighthouse" in text
    # missing file returns default content rather than raising
    missing = await ext.org_files.read_text("nope.txt")
    assert "Mock file content" in missing


# ---------------------------------------------------------------------------
# CLI: perceivo mock init / add
# ---------------------------------------------------------------------------


def test_cli_mock_init_and_add(tmp_path):
    runner = CliRunner()
    project = tmp_path / "proj"
    project.mkdir()
    (project / "agent.py").write_text(
        "import perceivo\nasync def f():\n    await perceivo.infra.milvus()\n", encoding="utf-8"
    )
    out = tmp_path / "perceivo.mock.yaml"
    res = runner.invoke(perceivo_cli, ["mock", "init", str(project), "--output", str(out)])
    assert res.exit_code == 0, res.output
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "milvus" in doc["mocks"]["seed"]

    res2 = runner.invoke(
        perceivo_cli,
        ["mock", "add", "mcp", "search_docs", "--value", '{"result": "x"}', "--file", str(out)],
    )
    assert res2.exit_code == 0, res2.output
    doc2 = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc2["mocks"]["seed"]["mcp"]["calls"]["search_docs"] == {"result": "x"}


def test_cli_mock_add_rejects_unknown_service(tmp_path):
    runner = CliRunner()
    res = runner.invoke(
        perceivo_cli, ["mock", "add", "bogus", "k", "--file", str(tmp_path / "m.yaml")]
    )
    assert res.exit_code != 0
    assert "Unknown service" in res.output


# ---------------------------------------------------------------------------
# REVIEW auto-resolution + run trace feedback
# ---------------------------------------------------------------------------


async def test_review_component_auto_approves():
    from perceivo.primitives import Artifact, ArtifactType, ReviewOutput
    from perceivo.runner._component_execution import execute_component

    class _Reviewer:
        async def run(self, inp):
            return ReviewOutput(response_data={"note": "kept"})

    res = await execute_component(
        _Reviewer(), "REVIEW", Artifact(type=ArtifactType.NONE, data=None), {}
    )
    assert res.success
    assert res.response_data["approved"] is True
    assert res.response_data["decision"] == "approved"
    # the component's own response_data is preserved alongside the decision
    assert res.response_data["note"] == "kept"


def test_run_cmd_result_dict_includes_trace():
    from perceivo._cli import run_cmd
    from perceivo.runner.types import ExecutionResult, NodeTrace

    result = ExecutionResult(
        success=True,
        trace=[
            NodeTrace(
                node="N",
                component_type="AGENT",
                mocks=[{"service": "llm", "operation": "invoke", "mode": "mock", "summary": ""}],
            )
        ],
        mock_calls=[{"service": "llm", "operation": "invoke", "mode": "mock"}],
    )
    payload = run_cmd._result_dict(result)
    assert payload["status"] == "PASS"
    assert payload["mock_call_count"] == 1
    assert payload["trace"][0]["node"] == "N"
