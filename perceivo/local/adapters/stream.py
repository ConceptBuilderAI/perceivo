"""The conversation channel of a local run, which never has one attached.

Each call answers as the platform does for a run dispatched without a channel:
``active`` is ``False``, ``token`` and ``send`` deliver nothing, ``peek`` finds
nothing, ``drain`` and ``history`` are empty, ``end`` publishes no frame and
``receive`` raises, because no message can ever arrive.
"""

from __future__ import annotations

from typing import Any, Optional

from ...types.errors import ContextError


class ChannelInactiveError(ContextError, RuntimeError):
    """``receive`` was called in a run with no conversation channel.

    Subclasses both ``ContextError`` and ``RuntimeError``, so either ``except``
    form catches it.
    """


class LocalStream:
    """The ``perceivo.stream`` hooks for a run with no conversation channel."""

    def conversation_is_active(self) -> bool:
        return False

    async def conversation_stream_token(self, text: str) -> None:
        return None

    async def conversation_send(
        self, parts: list[Any], metadata: Optional[dict[str, Any]] = None
    ) -> None:
        return None

    async def conversation_receive(self, timeout: Optional[float] = None) -> Optional[Any]:
        raise ChannelInactiveError("Conversation channel is not active")

    async def conversation_peek(self) -> Optional[Any]:
        return None

    async def conversation_drain(self) -> list[Any]:
        return []

    async def conversation_get_history(
        self, limit: int = 50, include_inbound: bool = True
    ) -> list[Any]:
        return []

    async def stream_end(self) -> int:
        return 0
