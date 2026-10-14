"""The organization's MCP tool server, reached as ``perceivo.cluster.mcp.connect()``.

The platform deploys MCP per organization exactly as it deploys redis, milvus
and elasticsearch, so it is reached the same way::

    mcp = await perceivo.cluster.mcp.connect()
    if await mcp.is_available():
        tools = await mcp.list_tools()
        result = await mcp.call_tool("search_documents", {"query": "revenue"})

The MCP server is optional infrastructure. Inside a run,
:meth:`MCPClient.is_available` answers rather than raising; the other two raise
when the server is not running. All
three go through the runtime's ``ExtensionManager``, which auto-connects on
first use.
"""

from typing import Any, cast

from .._internal.context import _ExecutionContext

__all__ = ["MCPClient", "get_mcp"]


class MCPClient:
    """The tool client ``perceivo.cluster.mcp.connect()`` returns.

    Holds no connection state of its own: every call resolves the run's
    execution context, so a client obtained once stays valid for the run.
    """

    async def is_available(self) -> bool:
        """
        Check if the organization's MCP server is running and reachable.

        Inside a run this does not raise: it returns False if the MCP server is
        disabled, not configured, not running, or the infrastructure check fails.

        Example:
            ```python
            mcp = await perceivo.cluster.mcp.connect()
            if await mcp.is_available():
                tools = await mcp.list_tools()
            ```
        """
        ext = _ExecutionContext.get_ext()
        return cast(bool, await ext.is_mcp_available())

    async def list_tools(self) -> list[Any]:
        """
        Discover available tools on the organization's MCP server.

        Returns:
            List of tool metadata dicts, each with ``name``, ``description``
            and ``input_schema``.

        Raises:
            RuntimeError: If MCP server is not running.

        Example:
            ```python
            mcp = await perceivo.cluster.mcp.connect()
            for tool in await mcp.list_tools():
                perceivo.log.info("Available tool", name=tool["name"])
            ```
        """
        ext = _ExecutionContext.get_ext()
        mcp_client = await ext.get_mcp_client()
        return cast(list[Any], await mcp_client.list_tools())

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call an MCP tool and return the result.

        Args:
            tool_name: Name of the tool to invoke (from :meth:`list_tools`).
            arguments: Tool arguments matching the tool's input_schema.

        Returns:
            Tool result as a dict (the tool's response_data).

        Raises:
            RuntimeError: If MCP server is not running.

        Example:
            ```python
            mcp = await perceivo.cluster.mcp.connect()
            result = await mcp.call_tool(
                "search_documents",
                arguments={"query": "quarterly revenue", "limit": 5},
            )
            documents = result.get("results", [])
            ```
        """
        ext = _ExecutionContext.get_ext()
        mcp_client = await ext.get_mcp_client()
        return cast(dict[str, Any], await mcp_client.call_tool(tool_name, arguments or {}))


async def get_mcp() -> MCPClient:
    """
    Get the tool client for the organization's MCP server.

    Returns an :class:`MCPClient` whether or not the server is running; ask
    :meth:`MCPClient.is_available` before relying on it.

    Example:
        ```python
        mcp = await perceivo.cluster.mcp.connect()
        ```
    """
    return MCPClient()
