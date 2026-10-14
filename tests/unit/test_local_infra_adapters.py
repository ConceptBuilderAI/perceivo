"""Unit tests for local infrastructure adapters."""

from __future__ import annotations

import re
import sys
import types

import pytest

from perceivo.local.adapters.infra import LocalInfraStubs

# ---------------------------------------------------------------------------


class _Cfg:
    """Minimal config object for LocalInfraStubs."""

    class _Svc:
        def __init__(self, enabled=False, uri="", url=""):
            self.enabled = enabled
            self.uri = uri
            self.url = url

    def __init__(self, dbs=None):
        self.milvus = self._Svc()
        self.elasticsearch = self._Svc()
        self.databases = dbs or []


def _stub_with_config(cfg):
    stub = LocalInfraStubs()
    stub._config = cfg
    stub._milvus_initialized = False
    stub._es_initialized = False
    stub._milvus_client = None
    stub._es_client = None
    return stub


async def test_infra_milvus_not_configured_raises():
    stub = _stub_with_config(_Cfg())
    with pytest.raises(RuntimeError, match="Milvus is not configured"):
        await stub.get_milvus()


async def test_infra_elasticsearch_not_configured_raises():
    stub = _stub_with_config(_Cfg())
    with pytest.raises(RuntimeError, match="Elasticsearch is not configured"):
        await stub.get_elasticsearch()


async def test_infra_database_not_configured_raises():
    stub = _stub_with_config(_Cfg())
    with pytest.raises(RuntimeError, match="is not configured"):
        await stub.resolve_platform_database("db1")
    with pytest.raises(RuntimeError, match="is not configured"):
        await stub.execute_database_query("db1", "SELECT 1")


async def test_infra_resolve_configured_database():
    class _DB:
        id = "db1"
        url = "postgresql://localhost/x"
        read_only = True

    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    resolved = await stub.resolve_platform_database("db1")
    assert resolved["database_id"] == "db1"
    assert resolved["read_only"] is True


async def test_infra_query_readonly_write_rejected():
    class _DB:
        id = "db1"
        url = "postgresql://localhost/x"
        read_only = True

    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    with pytest.raises(RuntimeError, match="configured as read_only"):
        await stub.execute_database_query("db1", "INSERT ...", read_only=False)


async def test_infra_cloud_only_services_raise():
    stub = _stub_with_config(_Cfg())
    # SPEC-5: with no ai_foundry config (BYO-LLM disabled), AI-Foundry calls raise
    # the unified "not available locally" error pointing at the enablement config.
    with pytest.raises(RuntimeError, match="AI Foundry is not available"):
        await stub.invoke_ai_foundry()
    with pytest.raises(RuntimeError, match="AI Foundry is not available"):
        await stub.embed_ai_foundry(model="embed-small", texts=["hello"])
    with pytest.raises(RuntimeError):
        await stub.list_ai_foundry_models()
    assert await stub.is_mcp_available() is False
    with pytest.raises(RuntimeError, match="MCP"):
        await stub.get_mcp_client()
    for getter in (
        stub.get_orchestration_summary,
        stub.get_capability_summary,
        stub.get_intelligence_summary,
        stub.get_billing_usage,
        stub.get_review_stats,
        stub.get_runs_over_time,
    ):
        with pytest.raises(RuntimeError, match="Metrics"):
            await getter()


async def test_infra_local_noops():
    stub = _stub_with_config(_Cfg())
    assert await stub.get_review_results() == []
    assert await stub.record_cost("x", 0.5) is None
    assert await stub.get_llm_pricing("openai", "gpt-4o") is None


# ---------------------------------------------------------------------------
# LocalInfraStubs — Milvus lazy-connect (config-aware)
# ---------------------------------------------------------------------------


def _fake_module(name: str, **attrs):
    """Build a throwaway module object to stand in for an optional dep."""
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _milvus_cfg(enabled=True, uri="http://localhost:19530"):
    cfg = _Cfg()
    cfg.milvus = _Cfg._Svc(enabled=enabled, uri=uri)
    return cfg


def _es_cfg(enabled=True, url="http://localhost:9200"):
    cfg = _Cfg()
    cfg.elasticsearch = _Cfg._Svc(enabled=enabled, url=url)
    return cfg


async def test_infra_milvus_connects_when_enabled_and_caches(monkeypatch):
    created: list[str] = []

    class FakeMilvusClient:
        def __init__(self, uri):
            created.append(uri)
            self.uri = uri

    monkeypatch.setitem(
        sys.modules, "pymilvus", _fake_module("pymilvus", MilvusClient=FakeMilvusClient)
    )
    stub = _stub_with_config(_milvus_cfg(uri="http://mv:19530"))
    client = await stub.get_milvus()
    assert isinstance(client, FakeMilvusClient)
    assert client.uri == "http://mv:19530"
    # Second call returns the cached client without re-constructing (line 56).
    assert await stub.get_milvus() is client
    assert created == ["http://mv:19530"]


async def test_infra_milvus_missing_package_raises(monkeypatch):
    # None in sys.modules makes `from pymilvus import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "pymilvus", None)
    stub = _stub_with_config(_milvus_cfg())
    with pytest.raises(RuntimeError, match="pymilvus package not installed"):
        await stub.get_milvus()


async def test_infra_milvus_connection_failure_wraps_error(monkeypatch):
    class BoomClient:
        def __init__(self, uri):
            raise ValueError("no route to host")

    monkeypatch.setitem(sys.modules, "pymilvus", _fake_module("pymilvus", MilvusClient=BoomClient))
    stub = _stub_with_config(_milvus_cfg(uri="http://mv:19530"))
    with pytest.raises(RuntimeError, match="Could not connect to Milvus at http://mv:19530"):
        await stub.get_milvus()


async def test_infra_milvus_returns_cached_client_when_initialized():
    # Already-initialized short-circuit returns the stored client (line 56).
    stub = _stub_with_config(_Cfg())
    sentinel = object()
    stub._milvus_initialized = True
    stub._milvus_client = sentinel
    assert await stub.get_milvus() is sentinel


# ---------------------------------------------------------------------------
# LocalInfraStubs — Elasticsearch lazy-connect (config-aware)
# ---------------------------------------------------------------------------


async def test_infra_elasticsearch_connects_when_enabled_and_caches(monkeypatch):
    created: list[str] = []

    class FakeES:
        def __init__(self, url):
            created.append(url)
            self.url = url

    monkeypatch.setitem(
        sys.modules,
        "elasticsearch",
        _fake_module("elasticsearch", AsyncElasticsearch=FakeES),
    )
    stub = _stub_with_config(_es_cfg(url="http://es:9200"))
    client = await stub.get_elasticsearch()
    assert isinstance(client, FakeES)
    assert client.url == "http://es:9200"
    # Cached on the second call (line 93).
    assert await stub.get_elasticsearch() is client
    assert created == ["http://es:9200"]


async def test_infra_elasticsearch_missing_package_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "elasticsearch", None)
    stub = _stub_with_config(_es_cfg())
    with pytest.raises(RuntimeError, match="elasticsearch package not installed"):
        await stub.get_elasticsearch()


async def test_infra_elasticsearch_connection_failure_wraps_error(monkeypatch):
    class BoomES:
        def __init__(self, url):
            raise ConnectionError("refused")

    monkeypatch.setitem(
        sys.modules,
        "elasticsearch",
        _fake_module("elasticsearch", AsyncElasticsearch=BoomES),
    )
    stub = _stub_with_config(_es_cfg(url="http://es:9200"))
    with pytest.raises(RuntimeError, match="Could not connect to Elasticsearch at http://es:9200"):
        await stub.get_elasticsearch()


async def test_infra_elasticsearch_returns_cached_client_when_initialized():
    stub = _stub_with_config(_Cfg())
    sentinel = object()
    stub._es_initialized = True
    stub._es_client = sentinel
    assert await stub.get_elasticsearch() is sentinel


# ---------------------------------------------------------------------------
# LocalInfraStubs — execute_database_query (asyncpg execution path)
# ---------------------------------------------------------------------------


class _DB:
    id = "db1"
    url = "postgresql://localhost/x"
    read_only = True


class _WritableDB:
    id = "db1"
    url = "postgresql://localhost/x"
    read_only = False


class _FakeRecord(dict):
    """Mimics an asyncpg Record: supports .keys() and dict(row)."""


def _patch_asyncpg_connect(monkeypatch, connect):
    monkeypatch.setitem(sys.modules, "asyncpg", _fake_module("asyncpg", connect=connect))


async def test_infra_execute_query_returns_rows(monkeypatch):
    closed: list[bool] = []
    rows = [_FakeRecord({"id": 1, "name": "a"}), _FakeRecord({"id": 2, "name": "b"})]

    class FakeConn:
        async def fetch(self, sql):
            assert sql == "SELECT * FROM t"
            return rows

        async def close(self):
            closed.append(True)

    async def fake_connect(url):
        assert url == "postgresql://localhost/x"
        return FakeConn()

    _patch_asyncpg_connect(monkeypatch, fake_connect)
    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    out = await stub.execute_database_query("db1", "SELECT * FROM t")
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [[1, "a"], [2, "b"]]
    assert out["row_count"] == 2
    assert out["truncated"] is False
    # Connection is always closed via the finally block.
    assert closed == [True]


async def test_infra_execute_query_truncates_at_max_rows(monkeypatch):
    rows = [_FakeRecord({"n": i}) for i in range(5)]

    class FakeConn:
        async def fetch(self, sql):
            return rows

        async def close(self):
            pass

    async def fake_connect(url):
        return FakeConn()

    _patch_asyncpg_connect(monkeypatch, fake_connect)
    stub = _stub_with_config(_Cfg(dbs=[_WritableDB()]))
    out = await stub.execute_database_query("db1", "SELECT n", read_only=False, max_rows=2)
    assert out["row_count"] == 2
    assert out["rows"] == [[0], [1]]
    assert out["truncated"] is True


async def test_infra_execute_query_missing_asyncpg_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "asyncpg", None)
    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    with pytest.raises(RuntimeError, match="asyncpg package not installed"):
        await stub.execute_database_query("db1", "SELECT 1")


async def test_infra_execute_query_wraps_backend_error(monkeypatch):
    async def boom_connect(url):
        raise OSError("connection refused")

    _patch_asyncpg_connect(monkeypatch, boom_connect)
    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    with pytest.raises(RuntimeError, match="Database query failed for 'db1'"):
        await stub.execute_database_query("db1", "SELECT 1")


# ---------------------------------------------------------------------------
# LocalInfraStubs — _find_database branch coverage
# ---------------------------------------------------------------------------


async def test_infra_find_database_skips_non_matching_id():
    class _Other:
        id = "other"
        url = "postgresql://localhost/x"
        read_only = True

    stub = _stub_with_config(_Cfg(dbs=[_Other()]))
    with pytest.raises(RuntimeError, match="is not configured"):
        await stub.resolve_platform_database("db1")


async def test_infra_find_database_with_no_config():
    stub = LocalInfraStubs()
    stub._config = None
    with pytest.raises(RuntimeError, match="is not configured"):
        await stub.resolve_platform_database("db1")


# ---------------------------------------------------------------------------
# LocalInfraStubs — execute_external_database_query
# ---------------------------------------------------------------------------


async def test_infra_external_query_unconfigured_raises():
    """The error must name the external connection, not a platform database."""
    stub = _stub_with_config(_Cfg(dbs=[]))
    with pytest.raises(RuntimeError, match="External database connection 'conn1'"):
        await stub.execute_external_database_query("conn1", "SELECT 1")


async def test_infra_external_query_unconfigured_shows_yaml_hint():
    stub = _stub_with_config(_Cfg(dbs=[]))
    with pytest.raises(RuntimeError, match=re.escape("perceivo.yaml")):
        await stub.execute_external_database_query("conn1", "SELECT 1")


async def test_infra_external_query_returns_rows(monkeypatch):
    rows = [_FakeRecord({"id": 1})]

    class FakeConn:
        async def fetch(self, sql):
            assert sql == "SELECT * FROM t"
            return rows

        async def close(self):
            pass

    async def fake_connect(url):
        assert url == "postgresql://localhost/x"
        return FakeConn()

    _patch_asyncpg_connect(monkeypatch, fake_connect)
    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    out = await stub.execute_external_database_query("db1", "SELECT * FROM t")
    assert out["rows"] == [[1]]
    assert out["row_count"] == 1


async def test_infra_external_query_honours_read_only_config(monkeypatch):
    """A read_only connection refuses a write, same as the platform path."""
    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    with pytest.raises(RuntimeError, match="read_only"):
        await stub.execute_external_database_query("db1", "DELETE FROM t", read_only=False)


async def test_infra_external_query_binds_params_locally(monkeypatch):
    """Was ``test_infra_external_query_ignores_params_locally``.

    ``params`` was advertised in the signature and the docstring and then
    silently dropped on every path — so a component author following the
    documented parameterized API got no parameterization, no error, and the
    natural workaround was f-string interpolation. Local mode has to bind them
    for real, or a component that works locally breaks in the cloud.
    """
    captured = {}

    class FakeConn:
        async def fetch(self, sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        async def close(self):
            pass

    async def fake_connect(url):
        return FakeConn()

    _patch_asyncpg_connect(monkeypatch, fake_connect)
    stub = _stub_with_config(_Cfg(dbs=[_DB()]))
    out = await stub.execute_external_database_query(
        "db1", "SELECT $1, $2", params=[1, 2], max_rows=5
    )
    assert out["row_count"] == 0
    assert captured["args"] == (1, 2)
