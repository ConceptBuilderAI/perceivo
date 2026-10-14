"""The MCP tool client ``perceivo.cluster.mcp.connect()`` returns."""

from __future__ import annotations

import pytest

import perceivo
from perceivo.cluster import _backend, _mcp_backend

mcp = _mcp_backend.MCPClient()


async def test_connect_is_the_backend_accessor_and_returns_the_tool_client():
    """``cluster.mcp`` is an ordinary cluster handle, derived like the others."""
    assert "mcp" in perceivo.cluster.__all__
    assert perceivo.cluster.mcp.connect is _backend.get_mcp
    client = await perceivo.cluster.mcp.connect()
    assert isinstance(client, _mcp_backend.MCPClient)
    for verb in ("is_available", "list_tools", "call_tool"):
        assert callable(getattr(client, verb))


class _MCPClientStub:
    def __init__(self, tools=None, call_result=None):
        self._tools = tools or []
        self._call_result = call_result or {}
        self.calls = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._call_result


async def test_is_available_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await mcp.is_available()


async def test_is_available_delegates_true(stub_ext):
    async def is_mcp_available():
        return True

    stub_ext.is_mcp_available = is_mcp_available
    assert await mcp.is_available() is True


async def test_is_available_delegates_false(stub_ext):
    async def is_mcp_available():
        return False

    stub_ext.is_mcp_available = is_mcp_available
    assert await mcp.is_available() is False


async def test_list_tools_delegates_via_client(stub_ext):
    tools = [{"name": "search", "input_schema": {}}]
    client = _MCPClientStub(tools=tools)

    async def get_mcp_client():
        return client

    stub_ext.get_mcp_client = get_mcp_client
    assert await mcp.list_tools() == tools


async def test_call_tool_delegates_arguments(stub_ext):
    client = _MCPClientStub(call_result={"hits": 3})

    async def get_mcp_client():
        return client

    stub_ext.get_mcp_client = get_mcp_client
    result = await mcp.call_tool("search", {"query": "Q3"})
    assert result == {"hits": 3}
    assert client.calls == [("search", {"query": "Q3"})]


async def test_call_tool_defaults_empty_arguments(stub_ext):
    client = _MCPClientStub(call_result={})

    async def get_mcp_client():
        return client

    stub_ext.get_mcp_client = get_mcp_client
    await mcp.call_tool("ping")
    assert client.calls == [("ping", {})]
