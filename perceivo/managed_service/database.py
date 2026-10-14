"""Managed database instances.

Usage::

    db = await managed_service.database.connect(instance_id)
    rows = await db.query(sql)

``database`` is a module, so it cannot be called; ``connect`` is the verb on
it.

- ``connect`` performs no I/O and holds no connection. It returns a handle
  carrying the instance id; nothing is resolved, opened or authenticated until
  ``query`` is called.
- ``query`` issues one platform call carrying ``(database_id, sql, read_only,
  max_rows)``. ``read_only`` and ``max_rows`` are per call, not bound at
  ``connect``.
- Bind parameters are not supported: the platform query has no parameter
  channel, so positional parameters raise ``NotImplementedError``.

Outside a run ``query`` raises ``RuntimeError``; inside the local runner a
mock database serves it.
"""

from __future__ import annotations

from typing import Any

from ._backend import database_query

#: Only ``connect``; the category grid carries no verbs.
__all__ = ["connect"]


class DatabaseClient:
    """A handle on one managed database instance.

    Obtain one with :func:`connect`; it is not exported.
    """

    __slots__ = ("instance_id",)

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id

    def __repr__(self) -> str:
        return f"DatabaseClient(instance_id={self.instance_id!r})"

    async def query(
        self,
        sql: str,
        *params: Any,
        read_only: bool = True,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        """Run one SQL statement against this instance.

        Args:
            sql: The statement to execute.
            *params: Not supported. Accepted in the signature only so that passing
                them raises an explanation instead of a bare ``TypeError``.
            read_only: If True, a statement whose first keyword, read past any
                ``EXPLAIN`` options, is a write or DDL keyword such as ``INSERT``
                or ``DROP`` is refused. The platform enforces more on top.
            max_rows: Maximum rows returned (the platform caps this at 10000).

        Returns:
            ``{"columns": [...], "rows": [...], "row_count": int, ...}``, forwarded
            unchanged from the platform.

        Raises:
            httpx.HTTPStatusError: A read-only query was refused. The status is
                400, and the response body's ``detail`` names the keyword.
            NotImplementedError: Bind parameters were passed; the platform query has
                no parameter channel.
            RuntimeError: Called outside an execution context.

        Example::

            db = await perceivo.managed_service.database.connect(instance_id)
            result = await db.query(
                "SELECT id, status FROM claims WHERE status = 'open'",
                max_rows=100,
            )
            for row in result["rows"]:
                print(row)
        """
        if params:
            raise NotImplementedError(
                f"managed_service.database.query() does not take bind "
                f"parameters ({len(params)} passed). The platform's "
                f"database_query endpoint has no parameter channel, and the "
                f"endpoint that does — external_database_query, against a "
                f"registered external connection — has no public "
                f"spelling. Pass one complete SQL "
                f"string. Quoting any value that reached the component from "
                f"outside is then yours to do, and this call will not "
                f"silently become the other endpoint."
            )
        return await database_query(
            self.instance_id,
            sql,
            read_only=read_only,
            max_rows=max_rows,
        )


async def connect(instance: str) -> DatabaseClient:
    """Return a handle on the managed database instance *instance*.

    Performs no I/O: nothing is resolved, opened or authenticated until
    :meth:`DatabaseClient.query` is called.

    Args:
        instance: The managed database instance id.

    Returns:
        A :class:`DatabaseClient` bound to *instance*.

    Example::

        db   = await perceivo.managed_service.database.connect(instance_id)
        rows = await db.query("SELECT * FROM claims WHERE status = 'open'")
    """
    return DatabaseClient(instance)
