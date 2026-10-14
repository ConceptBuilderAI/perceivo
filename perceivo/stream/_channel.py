"""Conversation channel functions.

Bidirectional streaming between components and end-users in chatbot
interfaces: streaming tokens outbound, sending structured messages, and
receiving user messages during execution. Available in orchestration
components. In a run with no channel attached, ``is_active`` is ``False``,
``token`` and ``send`` deliver nothing, ``peek`` finds nothing, ``drain`` and
``get_history`` are empty, and ``receive`` raises. Under an extension manager
with no conversation support (training, inference), every call raises.
"""

from typing import Any, Optional, cast

from .._internal.context import _ExecutionContext
from ..log.conversation import ContentPart, ConversationMessage


def _check_conversation_context(ext: Any, function_name: str) -> None:
    """Check that we're in a context that supports conversation channels."""
    if not hasattr(ext, "conversation_stream_token"):
        raise RuntimeError(
            f"perceivo.stream.{function_name}() is not available here: this extension "
            f"manager has no conversation support (training, inference, etc.)."
        )


async def token(text: str) -> None:
    """
    Push a single token to the conversation stream.

    Use this to stream incremental text to the user in real-time,
    typically during LLM generation. Tokens are buffered and flushed
    to the client over the active WebSocket/SSE connection.

    Args:
        text: The token text to stream. Can be a single character,
            a word, or a partial word: whatever the LLM yields.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).

    Example:
        ```python
        # Stream tokens as they arrive from an LLM
        async for token in llm_stream:
            await perceivo.stream.token(token)
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "token")
    await ext.conversation_stream_token(text)


async def send(
    parts: list[ContentPart],
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """
    Send a structured message to the user.

    Use this to send complete, structured messages (with typed content parts)
    to the user. Unlike token, this sends a fully-formed message that
    appears as a discrete entry in the conversation UI.

    Args:
        parts: List of ContentPart objects making up the message.
        metadata: Optional metadata dict (e.g., model info, token counts).

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).

    Example:
        ```python
        await perceivo.stream.send(
            parts=[
                perceivo.log.conversation.ContentPart("text/plain", "Here are the results:"),
                perceivo.log.conversation.ContentPart("application/json", {"score": 0.95}, name="metrics"),
            ],
            metadata={"model": "gpt-4o"}
        )
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "send")
    await ext.conversation_send(parts, metadata)


async def receive(timeout: Optional[float] = None) -> Optional[ConversationMessage]:
    """
    Block until a user message arrives.

    Waits for the next inbound message from the user on the conversation
    channel. Use this when a component needs user input before proceeding
    (e.g., a clarification question).

    Args:
        timeout: Maximum seconds to wait. None means wait indefinitely.
            If the timeout expires before a message arrives, returns None.

    Returns:
        A ConversationMessage from the user, or None if the timeout expired.

    Raises:
        RuntimeError: If called outside an orchestration with a conversation channel.

    Example:
        ```python
        await perceivo.stream.send(
            parts=[perceivo.log.conversation.ContentPart("text/plain", "Which option do you prefer?")]
        )
        response = await perceivo.stream.receive(timeout=60.0)
        if response is None:
            # User didn't respond in time
            ...
        else:
            user_text = response.text
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "receive")
    return cast(ConversationMessage | None, await ext.conversation_receive(timeout))


async def peek() -> Optional[ConversationMessage]:
    """
    Non-blocking check for a queued user message.

    Checks if there's a user message waiting in the inbound queue without
    blocking. Use this to opportunistically check for user input without
    interrupting execution flow.

    Returns:
        A ConversationMessage if one is queued, or None if the queue is empty.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).

    Example:
        ```python
        # Check if user sent a cancel message
        msg = await perceivo.stream.peek()
        if msg and "cancel" in msg.text.lower():
            return perceivo.primitives.AgentOutput(artifact=cancelled_artifact)
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "peek")
    return cast(ConversationMessage | None, await ext.conversation_peek())


async def drain() -> list[ConversationMessage]:
    """
    Get all queued user messages (non-blocking).

    Returns all messages currently in the inbound queue without blocking.
    The queue is emptied after this call. Use this to batch-process
    multiple user messages that arrived during a long operation.

    Returns:
        List of ConversationMessage objects. Empty list if no messages queued.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).

    Example:
        ```python
        # After a long computation, check all messages that arrived
        messages = await perceivo.stream.drain()
        for msg in messages:
            perceivo.log.info(f"User said: {msg.text}")
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "drain")
    return cast(list[ConversationMessage], await ext.conversation_drain())


async def get_history(limit: int = 50, include_inbound: bool = True) -> list[ConversationMessage]:
    """
    Retrieve conversation history from persistence.

    Returns past messages (both inbound user messages and outbound component
    messages) from the configured persistence backend (Loki by default).

    Args:
        limit: Maximum number of messages to return.
        include_inbound: If True, include user messages. If False, only
            component messages.

    Returns:
        List of ConversationMessage ordered chronologically.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).

    Example:
        ```python
        # Build LLM context from conversation history
        history = await perceivo.stream.history(limit=20)
        messages = [{"role": msg.role, "content": msg.text} for msg in history]
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "history")
    return cast(
        list[ConversationMessage], await ext.conversation_get_history(limit, include_inbound)
    )


def is_active() -> bool:
    """
    Check if a conversation channel is active for this run.

    Returns True if the current orchestration run has an active conversation
    channel (i.e., a user is connected via WebSocket/SSE). Use this to
    conditionally enable streaming behavior.

    Returns:
        True if a conversation channel is active, False otherwise.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).

    Example:
        ```python
        if await perceivo.stream.active():
            # Stream tokens to user
            async for token in llm_stream:
                await perceivo.stream.token(token)
        else:
            # Batch mode: just collect the full response
            response = await llm.complete()
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_conversation_context(ext, "active")
    return cast(bool, ext.conversation_is_active())
