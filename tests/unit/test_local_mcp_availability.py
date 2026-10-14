"""``mcp.is_available()`` answers False for a disabled local MCP server rather than raising."""

from __future__ import annotations

from pathlib import Path

import pytest

import perceivo
from perceivo.config import Config, FilesConfig, MocksConfig
from perceivo.local.context import LocalExtensionManager
from perceivo.types.errors import ConfigurationError


def _ext(tmp_path: Path, mode: str) -> LocalExtensionManager:
    return LocalExtensionManager(
        Config(
            files=FilesConfig(working_dir=str(tmp_path / "working"), org_dir=str(tmp_path / "org")),
            mocks=MocksConfig(modes={"mcp": mode}),
        )
    )


async def test_a_disabled_mcp_server_is_unavailable(tmp_path):
    ext = _ext(tmp_path, "disabled")
    ext.activate()
    try:
        client = await perceivo.cluster.mcp.connect()
        assert await client.is_available() is False
    finally:
        ext.deactivate()


async def test_a_disabled_mcp_server_still_refuses_a_tool_client(tmp_path):
    ext = _ext(tmp_path, "disabled")
    with pytest.raises(ConfigurationError, match=r"^Local service 'mcp' is disabled\. "):
        await ext.get_mcp_client()


async def test_a_mocked_mcp_server_is_available(tmp_path):
    ext = _ext(tmp_path, "mock")
    assert await ext.is_mcp_available() is True
