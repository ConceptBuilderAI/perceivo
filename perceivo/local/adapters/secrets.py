"""Local secrets adapter that reads a .env file or environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


def _load_dotenv(path: str) -> dict[str, str]:
    """Parse a .env file into a dict (no external dependency)."""
    result: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return result

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


class LocalSecretsService:
    """Secrets service that reads from .env files or environment variables."""

    def __init__(
        self,
        source: str = "dotenv",
        dotenv_path: str = ".env",
        inline: Optional[dict[str, str]] = None,
    ):
        self._source = source
        self._secrets: dict[str, str] = {}

        if source == "dotenv":
            self._secrets = _load_dotenv(dotenv_path)
        elif source == "inline":
            self._secrets = dict(inline or {})
        # "env" source reads directly from os.environ at fetch time

    async def fetch_secret_value(self, secret_id: Optional[str]) -> Optional[str]:
        """Return the value this source binds to *secret_id*, or ``None`` if it binds none.

        ``None`` means "not found here"; the caller decides what that means. Mock-mode
        substitution belongs to ``LocalExtensionManager.fetch_secret_value``, which
        records it, so a real value in ``.env`` always wins.

        A ``None`` *secret_id* returns ``None`` rather than raising.

        Args:
            secret_id: The secret identifier to look up, or ``None``.

        Returns:
            The bound value, or ``None``.
        """
        if secret_id is None:
            return None
        if secret_id in self._secrets:
            return self._secrets[secret_id]
        return os.environ.get(secret_id)

    def get_all_keys(self) -> list[Any]:
        """List all available secret keys."""
        keys = set(self._secrets.keys())
        return sorted(keys)
