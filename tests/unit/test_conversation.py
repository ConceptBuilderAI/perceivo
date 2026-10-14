"""
Tests for the perceivo.stream channel implementation.

Validates that:
- Each function delegates to the correct ext method
- Functions raise RuntimeError when no execution context is active
- Functions raise RuntimeError when ext lacks conversation support
- ConversationConfig has correct default values
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from perceivo._internal.context import _ExecutionContext
from perceivo.log.conversation import ContentPart, ConversationMessage
from perceivo.stream._channel import (
    drain,
    get_history,
    is_active,
    peek,
    receive,
    send,
    token,
)
from perceivo.stream._config import ConversationConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_context():
    """Ensure execution context is clean before and after each test."""
    _ExecutionContext.clear_all()
    yield
    _ExecutionContext.clear_all()


@pytest.fixture
def mock_ext():
    """Create a mock ExtensionManager with conversation methods."""
    ext = MagicMock()
    ext.conversation_stream_token = AsyncMock(return_value=None)
    ext.conversation_send = AsyncMock(return_value=None)
    ext.conversation_receive = AsyncMock(return_value=None)
    ext.conversation_peek = AsyncMock(return_value=None)
    ext.conversation_drain = AsyncMock(return_value=[])
    ext.conversation_get_history = AsyncMock(return_value=[])
    ext.conversation_is_active = MagicMock(return_value=True)
    return ext


@pytest.fixture
def mock_ext_no_conversation():
    """Create a mock ExtensionManager WITHOUT conversation methods."""
    ext = MagicMock(spec=[])  # empty spec — no attributes
    return ext


# ---------------------------------------------------------------------------
# Tests: No execution context (RuntimeError)
# ---------------------------------------------------------------------------


class TestNoExecutionContext:
    """Functions raise RuntimeError when called outside execution context."""

    @pytest.mark.asyncio
    async def test_stream_token_no_context(self):
        with pytest.raises(RuntimeError, match="not available outside component"):
            await token("hello")

    @pytest.mark.asyncio
    async def test_send_no_context(self):
        parts = [ContentPart("text/plain", "hello")]
        with pytest.raises(RuntimeError, match="not available outside component"):
            await send(parts)

    @pytest.mark.asyncio
    async def test_receive_no_context(self):
        with pytest.raises(RuntimeError, match="not available outside component"):
            await receive()

    @pytest.mark.asyncio
    async def test_peek_no_context(self):
        with pytest.raises(RuntimeError, match="not available outside component"):
            await peek()

    @pytest.mark.asyncio
    async def test_drain_no_context(self):
        with pytest.raises(RuntimeError, match="not available outside component"):
            await drain()

    @pytest.mark.asyncio
    async def test_get_history_no_context(self):
        with pytest.raises(RuntimeError, match="not available outside component"):
            await get_history()

    def test_is_active_no_context(self):
        with pytest.raises(RuntimeError, match="not available outside component"):
            is_active()


# ---------------------------------------------------------------------------
# Tests: Ext lacks conversation support (RuntimeError)
# ---------------------------------------------------------------------------


def _no_support(fn: str) -> str:
    return (
        rf"perceivo\.stream\.{fn}\(\) is not available here: "
        r"this extension manager has no conversation support"
    )


class TestNoConversationSupport:
    """Functions raise RuntimeError when ext doesn't have conversation methods."""

    @pytest.mark.asyncio
    async def test_stream_token_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        with pytest.raises(RuntimeError, match=_no_support("token")):
            await token("hello")

    @pytest.mark.asyncio
    async def test_send_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        parts = [ContentPart("text/plain", "hello")]
        with pytest.raises(RuntimeError, match=_no_support("send")):
            await send(parts)

    @pytest.mark.asyncio
    async def test_receive_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        with pytest.raises(RuntimeError, match=_no_support("receive")):
            await receive()

    @pytest.mark.asyncio
    async def test_peek_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        with pytest.raises(RuntimeError, match=_no_support("peek")):
            await peek()

    @pytest.mark.asyncio
    async def test_drain_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        with pytest.raises(RuntimeError, match=_no_support("drain")):
            await drain()

    @pytest.mark.asyncio
    async def test_get_history_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        with pytest.raises(RuntimeError, match=_no_support("history")):
            await get_history()

    def test_is_active_no_support(self, mock_ext_no_conversation):
        _ExecutionContext.set_ext(mock_ext_no_conversation)
        with pytest.raises(RuntimeError, match=_no_support("active")):
            is_active()


# ---------------------------------------------------------------------------
# Tests: Delegation to ext methods
# ---------------------------------------------------------------------------


class TestDelegation:
    """Each function calls the correct ext method with correct arguments."""

    @pytest.mark.asyncio
    async def test_stream_token_delegates(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        await token("hello")
        mock_ext.conversation_stream_token.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_send_delegates(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        parts = [ContentPart("text/plain", "hi")]
        metadata = {"model": "gpt-4o"}
        await send(parts, metadata)
        mock_ext.conversation_send.assert_awaited_once_with(parts, metadata)

    @pytest.mark.asyncio
    async def test_send_delegates_no_metadata(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        parts = [ContentPart("text/plain", "hi")]
        await send(parts)
        mock_ext.conversation_send.assert_awaited_once_with(parts, None)

    @pytest.mark.asyncio
    async def test_receive_delegates_with_timeout(self, mock_ext):
        expected = ConversationMessage(role="user", parts=[ContentPart("text/plain", "user reply")])
        mock_ext.conversation_receive = AsyncMock(return_value=expected)
        _ExecutionContext.set_ext(mock_ext)

        result = await receive(timeout=30.0)
        mock_ext.conversation_receive.assert_awaited_once_with(30.0)
        assert result == expected

    @pytest.mark.asyncio
    async def test_receive_delegates_no_timeout(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        await receive()
        mock_ext.conversation_receive.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_peek_delegates(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        result = await peek()
        mock_ext.conversation_peek.assert_awaited_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_peek_returns_message(self, mock_ext):
        msg = ConversationMessage(role="user", parts=[ContentPart("text/plain", "queued message")])
        mock_ext.conversation_peek = AsyncMock(return_value=msg)
        _ExecutionContext.set_ext(mock_ext)

        result = await peek()
        assert result == msg

    @pytest.mark.asyncio
    async def test_drain_delegates(self, mock_ext):
        messages = [
            ConversationMessage(role="user", parts=[ContentPart("text/plain", "msg1")]),
            ConversationMessage(role="user", parts=[ContentPart("text/plain", "msg2")]),
        ]
        mock_ext.conversation_drain = AsyncMock(return_value=messages)
        _ExecutionContext.set_ext(mock_ext)

        result = await drain()
        mock_ext.conversation_drain.assert_awaited_once()
        assert result == messages

    @pytest.mark.asyncio
    async def test_get_history_delegates_defaults(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        result = await get_history()
        mock_ext.conversation_get_history.assert_awaited_once_with(50, True)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_history_delegates_custom_args(self, mock_ext):
        _ExecutionContext.set_ext(mock_ext)
        await get_history(limit=10, include_inbound=False)
        mock_ext.conversation_get_history.assert_awaited_once_with(10, False)

    @pytest.mark.asyncio
    async def test_get_history_returns_messages(self, mock_ext):
        messages = [
            ConversationMessage(role="user", parts=[ContentPart("text/plain", "user msg")]),
            ConversationMessage(
                role="assistant", parts=[ContentPart("text/plain", "assistant msg")]
            ),
        ]
        mock_ext.conversation_get_history = AsyncMock(return_value=messages)
        _ExecutionContext.set_ext(mock_ext)

        result = await get_history(limit=20)
        assert result == messages
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"

    def test_is_active_delegates_true(self, mock_ext):
        mock_ext.conversation_is_active.return_value = True
        _ExecutionContext.set_ext(mock_ext)
        assert is_active() is True
        mock_ext.conversation_is_active.assert_called_once()

    def test_is_active_delegates_false(self, mock_ext):
        mock_ext.conversation_is_active.return_value = False
        _ExecutionContext.set_ext(mock_ext)
        assert is_active() is False


# ---------------------------------------------------------------------------
# Tests: ConversationConfig
# ---------------------------------------------------------------------------


class TestConversationConfig:
    """ConversationConfig dataclass has correct defaults and is configurable."""

    def test_defaults(self):
        config = ConversationConfig()
        assert config.persistence == "loki"
        assert config.history_ttl_seconds == 86400
        assert config.channel_prefix == "cb:conv"

    def test_custom_values(self):
        config = ConversationConfig(
            persistence="redis",
            history_ttl_seconds=3600,
            channel_prefix="myapp:chat",
        )
        assert config.persistence == "redis"
        assert config.history_ttl_seconds == 3600
        assert config.channel_prefix == "myapp:chat"

    def test_none_persistence(self):
        config = ConversationConfig(persistence="none")
        assert config.persistence == "none"
