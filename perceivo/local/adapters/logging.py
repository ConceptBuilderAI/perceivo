"""Local logging adapter that prints to the console."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Optional

logger = logging.getLogger("perceivo")


# Only the Level values map; any other name, including a logging attribute, is INFO.
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _most_recent(entries: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    # A slice from -0 is the whole list, so zero is handled before slicing.
    return entries[-limit:] if limit > 0 else []


class LocalLoggingService:
    """Logging service that writes to console (stdout/stderr)."""

    def __init__(self, level: str = "INFO", fmt: str = "console"):
        self._level = getattr(logging, level.upper(), logging.INFO)
        self._format = fmt
        self._conversation_logs: list[dict[str, Any]] = []
        self._log_entries: list[dict[str, Any]] = []

        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            if fmt == "json":
                handler.setFormatter(logging.Formatter("%(message)s"))
            else:
                handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(self._level)

    async def write(
        self,
        level: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        log_level = _LEVELS.get(str(level).lower(), logging.INFO)

        self._log_entries.append(
            {
                "level": level,
                "message": message,
                "metadata": metadata or {},
                "timestamp": time.time(),
            }
        )

        if self._format == "json":
            entry: dict[str, Any] = {"level": level, "message": message}
            if metadata:
                entry["metadata"] = metadata
            logger.log(log_level, json.dumps(entry))
        else:
            if metadata:
                logger.log(log_level, "%s  %s", message, metadata)
            else:
                logger.log(log_level, message)

    async def read(
        self,
        category: str = "extension",
        limit: int = 100,
        chain: bool = False,
    ) -> list[dict[str, Any]]:
        """Read log entries filtered by category.

        Local adapter filters the in-memory ``_log_entries`` store.
        For the ``conversation`` category it falls back to
        ``_conversation_logs`` so that entries added via
        ``add_conversation_log()`` are included.
        """
        if category == "conversation":
            return _most_recent(self._conversation_logs, limit)
        return _most_recent(
            [
                entry
                for entry in self._log_entries
                if entry.get("metadata", {}).get("log_category", "extension") == category
            ],
            limit,
        )

    async def get_conversation_logs(
        self,
        limit: int = 100,
        chain: bool = False,
    ) -> list[dict[str, Any]]:
        return _most_recent(self._conversation_logs, limit)

    def add_conversation_log(self, entry: dict[str, Any]) -> None:
        self._conversation_logs.append(entry)
