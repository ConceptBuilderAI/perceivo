"""Local database rows are arrays aligned with ``columns``, as the platform returns them."""

from __future__ import annotations

import sys
import types

from perceivo.config import Config, DatabaseConfig, FilesConfig
from perceivo.local.context import LocalExtensionManager
from perceivo.local.mocks.database import MockDatabase


async def test_mock_seeded_rows_are_arrays():
    db = MockDatabase({"rows": [{"id": 7, "name": "a"}, {"id": 8, "name": "b"}]})
    out = await db.query("db", "SELECT id, name FROM t")
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [[7, "a"], [8, "b"]]


async def test_mock_stored_rows_are_arrays_in_column_order():
    db = MockDatabase({})
    await db.query("db", "INSERT INTO t (b, a) VALUES (2, 1)", read_only=False)
    await db.query("db", "INSERT INTO t (a, b) VALUES (3, 4)", read_only=False)
    out = await db.query("db", "SELECT * FROM t")
    assert out["columns"] == ["b", "a"]
    assert out["rows"] == [[2, 1], [4, 3]]


async def test_real_driver_rows_are_arrays(tmp_path, monkeypatch):
    class _Record(dict):
        pass

    class _Conn:
        async def fetch(self, sql, *args):
            return [_Record({"id": 1, "name": "x"}), _Record({"id": 2, "name": "y"})]

        async def close(self):
            pass

    async def _connect(url):
        return _Conn()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))
    ext = LocalExtensionManager(
        Config(
            files=FilesConfig(working_dir=str(tmp_path / "w"), org_dir=str(tmp_path / "o")),
            databases=[DatabaseConfig(id="db1", url="postgresql://u@h/db", read_only=True)],
        )
    )
    out = await ext.execute_database_query("db1", "SELECT id, name FROM t")
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [[1, "x"], [2, "y"]]
