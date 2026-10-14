"""A local read-only database query refuses a blocked leading statement as the platform does."""

from __future__ import annotations

import sys
import types

import httpx
import pytest

from perceivo.config import Config, DatabaseConfig, FilesConfig
from perceivo.local.context import LocalExtensionManager

_BLOCKED = [
    ("INSERT INTO t (a) VALUES (1)", "INSERT"),
    ("  delete from t", "DELETE"),
    ("/* note */ DROP TABLE t", "DROP"),
    ("-- note\nTRUNCATE t", "TRUNCATE"),
    ("EXPLAIN ANALYZE INSERT INTO t (a) VALUES (1)", "INSERT"),
    ("explain verbose analyze update t set a = 1", "UPDATE"),
    ("DESCRIBE DELETE FROM t", "DELETE"),
]

_ALLOWED = [
    "SELECT 1",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT 'DROP TABLE t' AS a",
    "/* INSERT */ SELECT 1",
    "-- DELETE\nSELECT 1",
    "/* /* DROP */ */ SELECT 1",
    "SELECT $$DELETE$$",
    "EXPLAIN SELECT 1",
]


def _mocked(tmp_path) -> LocalExtensionManager:
    return LocalExtensionManager(
        Config(files=FilesConfig(working_dir=str(tmp_path / "w"), org_dir=str(tmp_path / "o")))
    )


def _real(tmp_path, monkeypatch, fetched: list[str]) -> LocalExtensionManager:
    class _Conn:
        async def fetch(self, sql, *args):
            fetched.append(sql)
            return []

        async def close(self):
            pass

    async def _connect(url):
        return _Conn()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))
    return LocalExtensionManager(
        Config(
            files=FilesConfig(working_dir=str(tmp_path / "w"), org_dir=str(tmp_path / "o")),
            databases=[DatabaseConfig(id="db1", url="postgresql://u@h/db", read_only=True)],
        )
    )


def _assert_refused(caught: pytest.ExceptionInfo[httpx.HTTPStatusError], word: str) -> None:
    assert caught.value.response.status_code == 400
    assert caught.value.response.json() == {
        "detail": f"Statement '{word}' not allowed in read-only mode"
    }


@pytest.mark.parametrize(("sql", "word"), _BLOCKED)
async def test_the_mock_refuses_a_blocked_statement(tmp_path, sql, word):
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _mocked(tmp_path).execute_database_query("db1", sql)
    _assert_refused(caught, word)


@pytest.mark.parametrize(("sql", "word"), _BLOCKED)
async def test_the_mock_external_query_refuses_a_blocked_statement(tmp_path, sql, word):
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _mocked(tmp_path).execute_external_database_query("db1", sql)
    _assert_refused(caught, word)


@pytest.mark.parametrize(("sql", "word"), _BLOCKED)
async def test_the_real_path_refuses_before_reaching_the_driver(tmp_path, monkeypatch, sql, word):
    fetched: list[str] = []
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _real(tmp_path, monkeypatch, fetched).execute_database_query("db1", sql)
    _assert_refused(caught, word)
    assert fetched == []


@pytest.mark.parametrize("sql", _ALLOWED)
async def test_the_mock_runs_a_read(tmp_path, sql):
    out = await _mocked(tmp_path).execute_database_query("db1", sql)
    assert out["read_only"] is True


@pytest.mark.parametrize("sql", _ALLOWED)
async def test_the_real_path_runs_a_read(tmp_path, monkeypatch, sql):
    fetched: list[str] = []
    await _real(tmp_path, monkeypatch, fetched).execute_database_query("db1", sql)
    assert fetched == [sql]


async def test_a_write_request_is_not_screened(tmp_path):
    out = await _mocked(tmp_path).execute_database_query(
        "db1", "INSERT INTO t (a) VALUES (1)", read_only=False
    )
    assert out["row_count"] == 1
