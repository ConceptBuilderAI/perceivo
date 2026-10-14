"""Provide the platform-database accessors behind :mod:`perceivo.managed_service`.

Each function is a one-line forward to the runtime's extension manager.
"""

from typing import Any, cast

from .._internal.context import _ExecutionContext

__all__ = ["database_query", "external_database_query", "resolve_platform_database"]


async def resolve_platform_database(
    database_id: str, required_privilege: str = "DATABASE_QUERY"
) -> dict[str, Any]:
    """Resolve connection details for a platform-provisioned PostgreSQL database.

    Validates that the current user has the required privilege on the database,
    then returns full connection details (including credentials) for use in
    database operations.

    Args:
        database_id: UUID of the platform database to connect to.
        required_privilege: The privilege required for the operation.
            Use "DATABASE_QUERY" for read-only operations (SELECT),
            or "DATABASE_WRITE" for mutations (INSERT, UPDATE, DELETE, DDL).

    Returns:
        Dict with connection details:
            - host: Database hostname
            - port: Database port
            - database_name: Name of the database
            - username: Database username
            - password: Database password
            - ssl_mode: SSL mode (typically "require")

    Raises:
        PermissionError: If the user lacks the required privilege.
        RuntimeError: If the database is not running.

    Example:
        There is no ``perceivo.managed_service.database.resolve``; call this
        function directly. Prefer :func:`database_query`, which runs the
        statement server-side and never hands a credential to the pod.

        ```python
        from perceivo.managed_service._backend import resolve_platform_database

        conn = await resolve_platform_database(
            "abc-123",
            required_privilege="DATABASE_QUERY",
        )
        # conn['host'], conn['port'], ... build a connection string.
        # conn['password'] is a live credential: never log it.
        ```
    """
    ext = _ExecutionContext.get_ext()
    return cast(
        dict[str, Any], await ext.resolve_platform_database(database_id, required_privilege)
    )


async def database_query(
    database_id: str,
    sql: str,
    read_only: bool = True,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Execute a SQL query against a platform-provisioned PostgreSQL database.

    The query runs server-side on the platform, which holds the database
    connection, so the pod needs no network path to the database.

    Args:
        database_id: UUID of the platform database.
        sql: SQL query to execute.
        read_only: If True, a statement whose first keyword, read past any
            ``EXPLAIN``/``DESCRIBE`` options, is a write or DDL keyword such as
            ``INSERT``, ``UPDATE``, ``DELETE``, ``DROP`` or ``CREATE`` is refused.
            Comments and string literals are ignored. The platform enforces more
            on top.
        max_rows: Maximum rows to return (capped at 10000).

    Returns:
        Dict with:
            - columns: List of column names
            - rows: List of row arrays
            - row_count: Number of rows returned
            - execution_time_ms: Query execution time
            - truncated: Whether results were truncated
            - read_only: Whether the query was read-only

    Raises:
        httpx.HTTPStatusError: A read-only query was refused. The status is 400,
            and the response body's ``detail`` names the statement keyword.

    Example:
        The public spelling is two steps: ``connect`` is a coroutine, so await
        it before using the client it returns. ``query`` takes the SQL
        positionally; the database is fixed by the connection.

        ```python
        db = await perceivo.managed_service.database.connect("abc-123")
        result = await db.query(
            "SELECT * FROM users WHERE active = true",
            max_rows=100,
        )
        for row in result['rows']:
            print(dict(zip(result['columns'], row)))
        ```
    """
    ext = _ExecutionContext.get_ext()
    return cast(
        dict[str, Any], await ext.execute_database_query(database_id, sql, read_only, max_rows)
    )


async def external_database_query(
    connection_id: str,
    sql: str,
    params: list[Any] | None = None,
    read_only: bool = True,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Execute a SQL query against a customer-owned ("external") database.

    Like :func:`database_query`, the query runs server-side: the platform holds
    the driver and the credential, and only rows come back. The database is
    addressed by the id of a connection registered for your organization. This
    function never takes a host, username, password or connection string, and a
    component pod never sees one.

    The platform validates the destination before connecting and applies the
    read-only controls: a read-only statement must start with ``SELECT``,
    ``WITH``, ``VALUES`` or ``TABLE`` and may contain no data-modifying keyword,
    only a single statement may be submitted, and results are always capped.

    ``params`` are bind parameters: they are handed to the driver and never
    interpolated into the statement text, so they cannot change its structure.
    Use the placeholder style of the connection's engine (``$1``, ``$2``, ... for
    PostgreSQL, ``%s`` for MySQL) and never build a value into the SQL string.

    Args:
        connection_id: UUID of the registered external database connection.
        sql: SQL query to execute, with placeholders for every value.
        params: Values bound to the placeholders in ``sql``, in order. Strings,
            numbers, booleans and ``None`` only; at most 256 of them.
        read_only: If True (default), only read statements are allowed. A
            connection registered as read-only refuses ``False`` outright.
        max_rows: Maximum rows to return (capped at 10000).

    Returns:
        Dict with:
            - columns: List of column names
            - rows: List of row arrays
            - row_count: Number of rows returned
            - execution_time_ms: Query execution time
            - truncated: Whether results were truncated
            - read_only: Whether the query was read-only

    Raises:
        PermissionError: If the user lacks the required privilege.
        RuntimeError: If the connection is inactive or unreachable.

    Example:
        This has no public spelling; call it directly.
        ``perceivo.managed_service.database.connect(...).query(...)`` is not an
        alternative: that path has no parameter channel and rejects bind
        parameters.

        ```python
        from perceivo.managed_service._backend import external_database_query

        result = await external_database_query(
            "8f14e45f-ceea-467a-9f2b-1c3d4e5f6a7b",
            "SELECT id, email FROM customers WHERE active = $1 AND region = $2",
            params=[True, "eu-west-1"],
            max_rows=100,
        )
        for row in result['rows']:
            print(dict(zip(result['columns'], row)))
        ```
    """
    ext = _ExecutionContext.get_ext()
    return cast(
        dict[str, Any],
        await ext.execute_external_database_query(connection_id, sql, params, read_only, max_rows),
    )
