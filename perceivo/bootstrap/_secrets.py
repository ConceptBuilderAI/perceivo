"""Notebook implementation of secrets access.

Routes secret resolution through the egress broker, which authenticates
via EXECUTION_TOKEN.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class SecretsService:
    """Secret resolution routed through the egress broker."""

    def __init__(
        self,
        egress_url: str,
        execution_token: str,
    ):
        self._egress_url = egress_url
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {execution_token}"},
        )

    async def fetch_secret_value(self, secret_id: str) -> Optional[str]:
        """Fetch a decrypted secret value by its UUID.

        Calls the egress broker's ``/internal/secrets-resolve/{secret_id}``
        endpoint.

        Args:
            secret_id: UUID of the org secret to fetch.

        Returns:
            Decrypted secret value string, or None on failure.
        """
        try:
            resp = await self._client.get(
                f"{self._egress_url}/internal/secrets-resolve/{secret_id}",
            )
            if resp.status_code == 404:
                # Do not log the secret id.
                logger.warning("Requested org secret was not found.")
                return None
            if resp.status_code == 403:
                logger.error(
                    "Insufficient privileges to read the requested secret. "
                    "Ensure the execution token has the 'secrets:read' capability."
                )
                return None
            resp.raise_for_status()
            data = resp.json()
            value: str | None = data.get("value")
            return value
        except Exception:
            # SECURITY: never log the exception, traceback or secret id here; the error
            # detail can embed the decrypted secret value.
            logger.error("Failed to resolve a secret via the egress broker")
            return None
