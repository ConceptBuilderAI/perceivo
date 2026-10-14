"""The write half of :mod:`perceivo.log`.

Writes go to the extension log (``LogType.EXTENSION``) and appear in the
Extension tab in the UI.
"""

import asyncio
from typing import Any, Optional

from .._internal.context import _ExecutionContext
from ..types.enums import Level

# asyncio holds only weak references to tasks; keep in-flight log writes alive
# until they complete.
_background_tasks: set[asyncio.Task[Any]] = set()


def _emit(level: str, message: str, metadata: Optional[dict[str, Any]]) -> None:
    """Write a log entry, using the running loop if there is one.

    Inside a running loop the write is fire-and-forget (a strong reference is
    retained until it completes); otherwise it runs to completion synchronously.
    """
    ext = _ExecutionContext.get_ext()
    coro = ext.logging.write(level, message, metadata)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    if loop.is_running():
        task = loop.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    else:
        loop.run_until_complete(coro)


async def flush_pending_writes() -> None:
    """Wait for every background log write scheduled on the running loop.

    The local runner awaits this after each node and before a run returns, so a
    component's log lines print in the order they were written, before the
    runner's own next line, and none is lost when the loop closes.
    """
    loop = asyncio.get_running_loop()
    while True:
        pending = [task for task in _background_tasks if task.get_loop() is loop]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def info(message: str, **fields: Any) -> None:
    """
    Log an info message.

    Info messages appear in the Extensions tab in the UI. Use for
    general progress updates and status information.

    Args:
        message: The log message.
        **fields: Structured context, sent as the entry's metadata.
            Passing none sends ``null`` rather than an empty object.

    Example:
        ```python
        perceivo.log.info("Processing started")
        perceivo.log.info("Processed file", filename="data.csv", rows=100)
        ```
    """
    _emit("info", message, fields or None)


def debug(message: str, **fields: Any) -> None:
    """
    Log a debug message.

    Debug messages appear in the Extensions tab in the UI. Use for
    detailed debugging information.

    Args:
        message: The log message.
        **fields: Structured context, sent as the entry's metadata.
            Passing none sends ``null`` rather than an empty object.

    Example:
        ```python
        perceivo.log.debug("Variable values", x=42, y="test")
        ```
    """
    _emit("debug", message, fields or None)


def warning(message: str, **fields: Any) -> None:
    """
    Log a warning message.

    Warning messages appear in the Extensions tab in the UI. Use for
    potential issues that don't prevent execution.

    Args:
        message: The log message.
        **fields: Structured context, sent as the entry's metadata.
            Passing none sends ``null`` rather than an empty object.

    Example:
        ```python
        perceivo.log.warning("Rate limit approaching", remaining=10)
        ```
    """
    _emit("warning", message, fields or None)


def error(message: str, **fields: Any) -> None:
    """
    Log an error message.

    Error messages appear in the Extensions tab in the UI. Use for
    errors that may affect execution but don't necessarily stop it.

    Args:
        message: The log message.
        **fields: Structured context, sent as the entry's metadata.
            Passing none sends ``null`` rather than an empty object.

    Example:
        ```python
        perceivo.log.error("Failed to parse response", error=str(e))
        ```
    """
    _emit("error", message, fields or None)


async def write(
    message: str,
    level: Level | str = Level.INFO,
    **fields: Any,
) -> None:
    """Write a log entry with a specific level.

    The awaited primitive the four convenience writers wrap. Use it when the
    level is chosen at runtime, or when the write must complete before the call
    returns; :func:`info` and the other convenience writers schedule the write as
    a background task inside a running loop.

    The level is not validated: an unrecognised string is logged at INFO. Pass a
    :class:`~perceivo.types.enums.Level` member to rule out misspellings.

    Args:
        message: The log message.
        level: A :class:`~perceivo.types.enums.Level` member, or its value.
            Defaults to ``Level.INFO``.
        **fields: Structured context, sent as the entry's metadata. Passing
            none sends ``null`` rather than an empty object.

    Example:
        ```python
        from perceivo.types import Level

        await perceivo.log.write("Processing complete")
        await perceivo.log.write("Retrying", level=Level.WARNING, attempt=3)
        ```
    """
    ext = _ExecutionContext.get_ext()
    await ext.logging.write(str(level), message, fields or None)
