"""The read half of :mod:`perceivo.log`: ``LogType``, ``LogEntry`` and ``query``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .._internal.context import _ExecutionContext
from ..types.errors import ContextError, PerceivoError
from ..types.pagination import Page

__all__ = ["LogEntry", "LogType", "query"]


class LogType(StrEnum):
    """The kinds of run log the platform writes.

    ``EXTENSION`` is the only one a component writes, through
    :func:`perceivo.log.info` and the other writers; the rest are written by the
    platform and are read-only.
    """

    EXTENSION = "extension"
    PROVIDER = "provider"
    STATE_TRANSITION = "state_transition"
    ERROR = "error"
    CONVERSATION = "conversation"
    CONVERSATION_INPUT = "conversation_input"
    CONVERSATION_STREAM = "conversation_stream"
    SNAPSHOT = "snapshot"

    @property
    def writable(self) -> bool:
        """True only for :attr:`EXTENSION`, the one log a component writes."""
        return self is LogType.EXTENSION


@dataclass(frozen=True)
class LogEntry:
    """One entry read back by :func:`query`.

    Extension entries carry ``level``, ``message``, ``metadata`` and
    ``timestamp``. Conversation entries carry ``role``, ``node_id``,
    ``component_name`` and ``parts``, with ``message`` holding their text.
    ``raw`` is the entry exactly as the runtime returned it.
    """

    log_type: LogType
    message: str = ""
    level: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] | None = None
    role: str | None = None
    node_id: str | None = None
    component_name: str | None = None
    parts: list[Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _entry(log_type: LogType, raw: Any) -> LogEntry:
    """Normalise one runtime entry into a :class:`LogEntry`."""
    if not isinstance(raw, dict):
        return LogEntry(log_type=log_type, message=str(raw), raw={"value": raw})
    message = raw.get("message")
    if message is None:
        message = raw.get("text", "")
    return LogEntry(
        log_type=log_type,
        message=str(message or ""),
        level=raw.get("level"),
        timestamp=raw.get("timestamp"),
        metadata=raw.get("metadata"),
        role=raw.get("role"),
        node_id=raw.get("node_id"),
        component_name=raw.get("component_name"),
        parts=raw.get("parts"),
        raw=dict(raw),
    )


class LogQueryArgumentError(PerceivoError, ValueError):
    """A ``query`` argument is out of range; raised before anything is read.

    Subclasses both ``PerceivoError`` and ``ValueError``, so either ``except``
    form catches it.
    """


async def query(
    log_type: LogType | str = LogType.CONVERSATION,
    *,
    limit: int = 100,
    chain: bool = False,
) -> Page[LogEntry]:
    """Read this run's log entries of one type.

    Args:
        log_type: A :class:`LogType` member or its value. Defaults to
            ``CONVERSATION``, the conversation the agents in this run wrote.
        limit: Maximum entries to return (the most recent ``limit``); ``0``
            returns none.
        chain: ``True`` reads across every run that shares this workload (the
            full history of a persistent, multi-turn workload) rather than
            this run only.

    Returns:
        A :class:`~perceivo.types.Page` of :class:`LogEntry`.

    Raises:
        LogQueryArgumentError: ``limit`` is negative.
        ContextError: ``log_type`` is not a :class:`LogType` value, or this
            runtime cannot read that log type. Every runtime reads
            ``CONVERSATION`` inside an orchestration; the local runner reads
            every type it has written.

    Example:
        ```python
        from perceivo.log import LogType

        page = await perceivo.log.query(LogType.CONVERSATION, limit=20)
        for entry in page:
            print(entry.role, entry.message)
        ```
    """
    if limit < 0:
        raise LogQueryArgumentError(f"log.query() got limit={limit}; limit must be 0 or more.")
    try:
        member = LogType(log_type)
    except ValueError:
        allowed = ", ".join(t.value for t in LogType)
        raise ContextError(
            f"log.query() got log_type={log_type!r}, which is not a LogType value. "
            f"Allowed: {allowed}."
        ) from None
    logging = _ExecutionContext.get_ext().logging
    reader = getattr(logging, "read", None)
    if reader is not None:
        raw = await reader(category=member.value, limit=limit, chain=chain)
    elif member is LogType.CONVERSATION and hasattr(logging, "get_conversation_logs"):
        raw = await logging.get_conversation_logs(limit, chain=chain)
    else:
        raise ContextError(
            f"log.query({member.value!r}) is not readable in this runtime. Inside an "
            f"orchestration the platform serves LogType.CONVERSATION; the other "
            f"types are read through the platform's run logs."
        )
    return Page(data=[_entry(member, r) for r in raw or []], page=1, page_size=limit)
