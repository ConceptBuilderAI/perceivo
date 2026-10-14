"""Notebook implementation of LoggingService.

In notebook context, logs go to Python's standard logging, which Jupyter
displays as cell output.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("perceivo.bootstrap")


class LoggingService:
    """Logging service for notebook context; writes to the Python logger."""

    async def write(
        self,
        level: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Write a log entry."""
        log_fn = getattr(logger, level.lower(), logger.info)
        if metadata:
            log_fn("[perceivo] %s | %s", message, metadata)
        else:
            log_fn("[perceivo] %s", message)

    async def get_conversation_logs(
        self,
        limit: int = 100,
        chain: bool = False,
    ) -> list[dict[str, Any]]:
        """Not available in notebook context."""
        logger.warning("get_conversation_logs() is only available in orchestration context")
        return []
