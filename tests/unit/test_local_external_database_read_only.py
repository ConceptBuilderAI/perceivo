"""A local read-only external-database query is refused as the platform refuses it.

The external path adds three controls to the platform-database rule: one
statement per request, a read leading keyword, and no write keyword anywhere.
A platform-database query keeps the leading-keyword rule alone.
"""

from __future__ import annotations

import sys
import types

import httpx
import pytest

from perceivo.config import Config, DatabaseConfig, FilesConfig
from perceivo.local.context import LocalExtensionManager

_NOT_READ = (
    "Statement 'SHOW' is not a read statement. Read-only queries must begin with "
    "one of: SELECT, WITH, VALUES, TABLE."
)

_BLOCKED_DELETE = "Statement 'DELETE' not allowed in read-only mode"


def _keyword(word: str) -> str:
    return (
        f"Keyword '{word}' is not allowed in a read-only query "
        "(including inside a common table expression)."
    )


_REFUSED = [
    ("SHOW server_version", _NOT_READ),
    ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", _keyword("DELETE")),
    ("SELECT * INTO t2 FROM t", _keyword("INTO")),
    ("SELECT 1; DROP TABLE t", "Only a single SQL statement may be submitted per request"),
    # The platform upper-cases before blanking literals, so $a$ and $A$ are one tag.
    ("$a$ $A$ DELETE $a$ SELECT 1", _BLOCKED_DELETE),
]

_READS = ["SELECT 1", "SELECT 1;", "SELECT 'a;b' AS v"]


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


def _assert_refused(caught: pytest.ExceptionInfo[httpx.HTTPStatusError], detail: str) -> None:
    assert caught.value.response.status_code == 400
    assert caught.value.response.json() == {"detail": detail}


@pytest.mark.parametrize(("sql", "detail"), _REFUSED)
async def test_the_mock_external_query_refuses(tmp_path, sql, detail):
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _mocked(tmp_path).execute_external_database_query("db1", sql)
    _assert_refused(caught, detail)


@pytest.mark.parametrize(("sql", "detail"), _REFUSED)
async def test_the_real_external_path_refuses_before_the_driver(tmp_path, monkeypatch, sql, detail):
    fetched: list[str] = []
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _real(tmp_path, monkeypatch, fetched).execute_external_database_query("db1", sql)
    _assert_refused(caught, detail)
    assert fetched == []


@pytest.mark.parametrize("sql", _READS)
async def test_the_real_external_path_runs_a_read(tmp_path, monkeypatch, sql):
    fetched: list[str] = []
    await _real(tmp_path, monkeypatch, fetched).execute_external_database_query("db1", sql)
    assert fetched == [sql]


async def test_a_platform_database_query_keeps_the_leading_keyword_rule(tmp_path):
    out = await _mocked(tmp_path).execute_database_query("db1", "SHOW server_version")
    assert out["read_only"] is True


async def test_a_platform_database_query_folds_case_before_blanking_literals(tmp_path):
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _mocked(tmp_path).execute_database_query("db1", "$a$ $A$ DELETE $a$ SELECT 1")
    _assert_refused(caught, _BLOCKED_DELETE)
