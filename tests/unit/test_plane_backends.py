"""The service accessors behind ``perceivo.cluster`` and ``perceivo.managed_service``.

The accessors live in the two planes that own them:
:mod:`perceivo.cluster._backend` holds redis / milvus / elasticsearch and
:mod:`perceivo.managed_service._backend` holds the three database calls.

``cluster.<svc>.connect is _backend.get_<svc>`` is asserted against the live
registry in ``tests/unit/test_cluster_registry.py`` rather than here.
"""

from __future__ import annotations

import pytest

from perceivo.cluster import _backend as cluster_backend
from perceivo.managed_service import _backend as database_backend


async def test_get_redis_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await cluster_backend.get_redis()


async def test_get_redis_delegates_to_ext(stub_ext):
    sentinel = object()

    async def get_redis():
        return sentinel

    stub_ext.get_redis = get_redis
    assert await cluster_backend.get_redis() is sentinel


async def test_redis_documented_example_runs_with_runtime_adapter(stub_ext):
    """Execute the published example against the runtime's TTL contract."""
    import ast
    import inspect
    import textwrap

    import perceivo

    writes = []
    reads = []

    class RedisAdapter:
        async def set(self, key, value, ttl_seconds=None):
            writes.append((key, value, ttl_seconds))

        async def get(self, key):
            reads.append(key)
            return "my_value"

    async def get_redis():
        return RedisAdapter()

    stub_ext.get_redis = get_redis
    doc = inspect.getdoc(cluster_backend.get_redis)
    assert doc is not None
    example = textwrap.dedent(doc.split("```python\n", 1)[1].split("```", 1)[0])
    compiled = compile(example, "redis-example", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    await eval(compiled, {"perceivo": perceivo})
    assert writes == [("my_key", "my_value", None), ("cache_key", "data", 3600)]
    assert reads == ["my_key"]


async def test_get_milvus_delegates(stub_ext):
    sentinel = object()

    async def get_milvus():
        return sentinel

    stub_ext.get_milvus = get_milvus
    assert await cluster_backend.get_milvus() is sentinel


async def test_get_elasticsearch_delegates(stub_ext):
    sentinel = object()

    async def get_elasticsearch():
        return sentinel

    stub_ext.get_elasticsearch = get_elasticsearch
    assert await cluster_backend.get_elasticsearch() is sentinel


async def test_resolve_platform_database_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await database_backend.resolve_platform_database("db-123")


async def test_resolve_platform_database_delegates_with_defaults(stub_ext):
    seen = {}

    async def resolve_platform_database(database_id, required_privilege):
        seen["database_id"] = database_id
        seen["required_privilege"] = required_privilege
        return {"host": "h", "port": 5432}

    stub_ext.resolve_platform_database = resolve_platform_database
    out = await database_backend.resolve_platform_database("db-1")
    assert seen == {"database_id": "db-1", "required_privilege": "DATABASE_QUERY"}
    assert out == {"host": "h", "port": 5432}


async def test_resolve_platform_database_passes_required_privilege(stub_ext):
    seen = {}

    async def resolve_platform_database(database_id, required_privilege):
        seen["required_privilege"] = required_privilege
        return {}

    stub_ext.resolve_platform_database = resolve_platform_database
    await database_backend.resolve_platform_database("db-1", required_privilege="DATABASE_WRITE")
    assert seen["required_privilege"] == "DATABASE_WRITE"


async def test_database_query_delegates_with_defaults(stub_ext):
    seen = {}

    async def execute_database_query(database_id, sql, read_only, max_rows):
        seen.update(
            database_id=database_id,
            sql=sql,
            read_only=read_only,
            max_rows=max_rows,
        )
        return {"columns": ["c"], "rows": [[1]], "row_count": 1}

    stub_ext.execute_database_query = execute_database_query
    out = await database_backend.database_query("db-1", "SELECT 1")
    assert seen == {
        "database_id": "db-1",
        "sql": "SELECT 1",
        "read_only": True,
        "max_rows": 1000,
    }
    assert out["row_count"] == 1


async def test_database_query_respects_overrides(stub_ext):
    seen = {}

    async def execute_database_query(database_id, sql, read_only, max_rows):
        seen.update(read_only=read_only, max_rows=max_rows)
        return {}

    stub_ext.execute_database_query = execute_database_query
    await database_backend.database_query("db", "DELETE FROM t", read_only=False, max_rows=42)
    assert seen == {"read_only": False, "max_rows": 42}


# ---------------------------------------------------------------------------
# external_database_query
# ---------------------------------------------------------------------------


async def test_external_database_query_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await database_backend.external_database_query("conn-1", "SELECT 1")


async def test_external_database_query_delegates_with_defaults(stub_ext):
    seen = {}

    async def execute_external_database_query(connection_id, sql, params, read_only, max_rows):
        seen.update(
            connection_id=connection_id,
            sql=sql,
            params=params,
            read_only=read_only,
            max_rows=max_rows,
        )
        return {"columns": ["c"], "rows": [[1]], "row_count": 1}

    stub_ext.execute_external_database_query = execute_external_database_query
    out = await database_backend.external_database_query("conn-1", "SELECT 1")
    assert seen == {
        "connection_id": "conn-1",
        "sql": "SELECT 1",
        "params": None,
        "read_only": True,
        "max_rows": 1000,
    }
    assert out["row_count"] == 1


async def test_external_database_query_respects_overrides(stub_ext):
    seen = {}

    async def execute_external_database_query(connection_id, sql, params, read_only, max_rows):
        seen.update(params=params, read_only=read_only, max_rows=max_rows)
        return {}

    stub_ext.execute_external_database_query = execute_external_database_query
    await database_backend.external_database_query(
        "conn", "DELETE FROM t", params=[1], read_only=False, max_rows=42
    )
    assert seen == {"params": [1], "read_only": False, "max_rows": 42}


async def test_external_database_query_takes_an_id_not_credentials():
    """The signature must never accept a host/user/password/connection string."""
    import inspect

    names = set(inspect.signature(database_backend.external_database_query).parameters)
    assert names == {"connection_id", "sql", "params", "read_only", "max_rows"}
    assert not names & {"host", "username", "password", "connection_string", "dsn"}


async def test_external_database_query_is_exported():
    assert "external_database_query" in database_backend.__all__
