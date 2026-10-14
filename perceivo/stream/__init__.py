"""The live channel to whoever drove this run.

Outbound, :func:`token` streams incremental text, :func:`send` sends a
structured message and :func:`end` closes the stream. Inbound,
:func:`receive` waits for the next user message, :func:`peek` checks for one
without blocking and :func:`drain` takes every queued message.
:func:`history` reads past messages from persistence and :func:`active`
reports whether a channel is attached.

No member takes a channel argument: the destination is always resolved
server-side from the run's own context, so a component cannot address
another run's stream.
"""

from __future__ import annotations

from .._internal.context import _ExecutionContext
from ..log.conversation import ConversationMessage
from ..types.errors import ContextError
from ..types.pagination import Page
from ._channel import drain, peek, receive, send, token
from ._channel import get_history as _get_history

__all__ = [
    "active",
    "drain",
    "end",
    "history",
    "peek",
    "receive",
    "send",
    "token",
]


async def active() -> bool:
    """Return ``True`` if a conversation channel is attached to this run.

    Await the call: an un-awaited coroutine is always truthy, so
    ``if perceivo.stream.active():`` takes the true branch even when no channel
    is attached.

    Returns:
        ``True`` if a conversation channel is active, ``False`` otherwise.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).
    """
    from ._channel import is_active as _is_active

    return _is_active()


async def end() -> None:
    """Close this run's stream by emitting its terminal frame.

    Ending is explicit and component-owned: nothing in the runner emits this
    frame, so a component that streams and never calls ``end`` leaves the reader
    waiting until its idle timeout. It is also repeatable: a component may
    stream several generations and end once, or end after each, and neither is
    an error. The destination is resolved server-side from the run's own
    context; there is no channel parameter.

    Raises:
        ~perceivo.types.errors.ContextError: This run's extension manager cannot
            end a stream, for example in an ML service extension (training,
            inference).
        RuntimeError: If called outside component execution context.

    Example:
        ```python
        async for tok in llm_stream:
            await perceivo.stream.token(tok)
        await perceivo.stream.end()
        ```
    """
    ext = _ExecutionContext.get_ext()
    if not hasattr(ext, "stream_end"):
        raise ContextError(
            "perceivo.stream.end() is only available in orchestrations whose "
            "extension manager can end a stream. It is not supported in ML "
            "service extensions (training, inference, etc.)."
        )
    await ext.stream_end()


async def history(limit: int = 50, include_inbound: bool = True) -> Page[ConversationMessage]:
    """Return past messages on this conversation, from persistence.

    The result is a single page: ``total`` is ``0`` (not reported) and
    ``has_next`` is ``False``.

    Args:
        limit: Maximum number of messages.
        include_inbound: Include the user's messages as well as the component's.

    Returns:
        A :class:`~perceivo.types.pagination.Page` of the messages returned.

    Raises:
        RuntimeError: If called outside component execution context, or under an
            extension manager with no conversation support (training, inference).
    """
    return Page(
        data=await _get_history(limit=limit, include_inbound=include_inbound),
        page=1,
        page_size=limit,
    )
