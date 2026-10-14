"""Mock MCP client: canned tool discovery and invocation for local runs.

The MCP server does not run locally, so the mock is the only local path.
``list_tools`` returns the seeded tool catalog and ``call_tool`` returns a
per-tool seeded result or the generic default.
"""

from __future__ import annotations

from typing import Any, Optional


class MockMCPClient:
    """In-memory stand-in for the organization's MCP server."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        return list(self._seed.get("tools") or [])

    async def call_tool(
        self, tool_name: str, arguments: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        overrides = self._seed.get("calls") or {}
        if tool_name in overrides:
            return dict(overrides[tool_name])
        default = self._seed.get("call") or {"result": "mock MCP tool result"}
        return {**default, "tool": tool_name, "arguments": arguments or {}}
