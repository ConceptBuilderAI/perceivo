"""Mock platform database: canned query results for local runs.

Stands in for the platform database behind ``perceivo.managed_service.database``
so orchestrations that read or write a platform-provisioned PostgreSQL database
run offline. Writes are accepted and reported as affecting rows; reads return
the seeded columns and rows.
"""

from __future__ import annotations

import re
from typing import Any

_INSERT_RE = re.compile(
    r"insert\s+into\s+([^\s(]+)\s*\(([^)]*)\)\s*values\s*\((.*)\)",
    re.IGNORECASE | re.DOTALL,
)
_SELECT_FROM_RE = re.compile(r"from\s+([^\s;]+)", re.IGNORECASE)


def _split_values(raw: str) -> list[str]:
    """Split a SQL VALUES tuple on top-level commas (respecting quotes)."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current = ""
    for ch in raw:
        if quote:
            current += ch
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current += ch
        elif ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def _coerce_literal(token: str) -> Any:
    """Best-effort conversion of a SQL literal to a Python value."""
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    low = token.lower()
    if low in ("true", "false"):
        return low == "true"
    if low == "null":
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


class MockDatabase:
    """In-memory stand-in for platform-provisioned databases.

    Naive but stateful: an ``INSERT`` is parsed into a row and stored per table, so
    a later ``SELECT`` from the same table reads it back. Unparseable reads fall
    back to the seeded rows.
    """

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}
        # table name -> list of row dicts inserted this run
        self._tables: dict[str, list[dict[str, Any]]] = {}

    async def resolve(
        self, database_id: str, required_privilege: str = "DATABASE_QUERY"
    ) -> dict[str, Any]:
        """Return the canned descriptor a real ``resolve`` would return.

        The values are placeholders that address no database: nothing in this class
        opens a connection, and the host ``mock-db`` does not resolve. The dict gives a
        component the same shape the platform returns. Do not add a password to the
        URI.
        """
        return {
            "database_id": database_id,
            "host": "mock-db",
            "port": 5432,
            "database_name": "mock",
            "username": "mock",
            "password": "mock",
            "ssl_mode": "disable",
            # Placeholder URI for an unreachable host, never handed to a driver.
            "url": f"postgresql://mock@mock-db:5432/{database_id}",  # NOSONAR
            "read_only": False,
            "privilege": required_privilege,
        }

    async def query(
        self,
        database_id: str,
        sql: str,
        read_only: bool = True,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        statement = (sql or "").strip()
        lowered = statement.lower()
        is_read = lowered.startswith(("select", "with"))
        if not is_read:
            self._apply_write(statement)
            return {
                "columns": [],
                "rows": [],
                # A mock write always reports one affected row.
                "row_count": 1,
                "execution_time_ms": 0,
                "truncated": False,
                "read_only": read_only,
            }
        rows = self._select_rows(statement)
        rows = rows[:max_rows]
        columns = (
            list(rows[0].keys()) if rows else list(self._seed.get("columns") or ["id", "name"])
        )
        return {
            "columns": columns,
            "rows": [[row.get(column) for column in columns] for row in rows],
            "row_count": len(rows),
            "execution_time_ms": 0,
            "truncated": False,
            "read_only": read_only,
        }

    def _apply_write(self, statement: str) -> None:
        """Parse and store an INSERT. Anything else is accepted and dropped."""
        match = _INSERT_RE.search(statement)
        if not match:
            return
        table = match.group(1).strip().strip('"').lower()
        columns = [c.strip().strip('"') for c in match.group(2).split(",") if c.strip()]
        values = [_coerce_literal(v) for v in _split_values(match.group(3))]
        row = {col: values[i] if i < len(values) else None for i, col in enumerate(columns)}
        self._tables.setdefault(table, []).append(row)

    def _select_rows(self, statement: str) -> list[dict[str, Any]]:
        """Return rows for the SELECT's table (stored inserts, else seed)."""
        match = _SELECT_FROM_RE.search(statement)
        if match:
            table = match.group(1).strip().strip('"').lower()
            stored = self._tables.get(table)
            if stored:
                return list(stored)
        return list(self._seed.get("rows") or [{"id": 1, "name": "mock-row"}])
