"""Notebook session: a lightweight execution context for the perceivo SDK.

Creates an object that satisfies the ``_ExecutionContext.get_ext()``
interface so that all perceivo SDK modules work transparently.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .._internal.context import _ExecutionContext
from ._files import FilesService
from ._logging import LoggingService
from ._org_files import OrgFilesService
from ._secrets import SecretsService

logger = logging.getLogger(__name__)

# Module-level session reference for disconnect()
_active_session: Optional[Session] = None


class Session:
    """Lightweight execution context for notebooks and standalone scripts.

    Provides the same interface as ExtensionManager for the subset of
    services relevant outside orchestration (org_files, logging, etc.).
    """

    def __init__(
        self,
        egress_url: str,
        execution_token: str,
        organization_id: str,
    ):
        self.organization_id = organization_id

        # Same attribute names as ExtensionManager.
        self.org_files = OrgFilesService(
            egress_url=egress_url,
            execution_token=execution_token,
            organization_id=organization_id,
        )
        self.logging = LoggingService()
        self._secrets_service = SecretsService(
            egress_url=egress_url,
            execution_token=execution_token,
        )

        # Secret info: empty in notebook mode (no manifest bindings).
        self._node_secret_info: dict[str, Any] = {
            "declarations": {},
            "aliases": {},
        }

        # Path-based files adapter over org_files.
        self.files = FilesService(self.org_files)

        # Stubs for modules that may try to access these
        self.secrets_manager = None

    def get_node_secret_info(self) -> dict[str, Any]:
        """Return secret info; always empty in notebook mode, which has no manifest bindings."""
        return self._node_secret_info

    async def fetch_secret_value(self, secret_id: str) -> Optional[str]:
        """Fetch a secret value by UUID via the egress broker."""
        return await self._secrets_service.fetch_secret_value(secret_id)

    def activate(self) -> None:
        """Push this context onto the SDK execution stack."""
        _ExecutionContext.set_ext(self)
        logger.info(
            "perceivo SDK connected (notebook mode, org=%s)",
            self.organization_id,
        )

    def deactivate(self) -> None:
        """Pop this context from the SDK execution stack."""
        _ExecutionContext.clear()
        logger.info("perceivo SDK disconnected")


def connect(
    *,
    egress_url: Optional[str] = None,
    execution_token: Optional[str] = None,
    organization_id: Optional[str] = None,
    config_path: Optional[str] = None,
    local: bool = False,
) -> Session:
    """Initialize the perceivo SDK for notebook, standalone, or local use.

    When called with no arguments, auto-detects the platform environment
    from environment variables set by the experiment kernel ConfigMap.

    For **local development** (outside the platform), pass ``config_path``
    pointing to a ``perceivo.yaml`` file, or ``local=True`` to load the file
    ``PERCEIVO_CONFIG`` names, else ``./perceivo.yaml``, with environment
    overrides applied; defaults fill whatever neither sets::

        await perceivo.bootstrap.start(config_path="perceivo.yaml")
        await perceivo.bootstrap.start(local=True)

    Args:
        egress_url: Egress broker URL. Auto-detected from
            ``EGRESS_BROKER_URL`` if not provided.
        execution_token: Execution token for broker auth. Auto-detected
            from ``EXECUTION_TOKEN`` if not provided.
        organization_id: Organization UUID. Auto-detected from
            ``ORGANIZATION_ID`` if not provided.
        config_path: Path to a YAML config file for local development.
        local: If True, use local development mode, loading the file
            ``PERCEIVO_CONFIG`` names, else ``./perceivo.yaml``.

    Returns:
        The active session (Session for platform, or
        LocalExtensionManager for local development).
    """
    global _active_session

    # Local development mode
    if config_path is not None or local:
        from ..local.context import LocalExtensionManager

        ext = LocalExtensionManager.from_config(config_path)
        ext.activate()
        _active_session = ext  # type: ignore[assignment]
        return ext  # type: ignore[return-value]

    # Auto-detect platform environment
    if egress_url is None:
        egress_url = os.environ.get("EGRESS_BROKER_URL")
    if execution_token is None:
        execution_token = os.environ.get("EXECUTION_TOKEN")
    if organization_id is None:
        organization_id = os.environ.get("ORGANIZATION_ID")

    if not egress_url:
        raise RuntimeError(
            "Cannot connect: EGRESS_BROKER_URL not set. "
            "Either pass egress_url= explicitly or run inside a platform experiment kernel."
        )
    if not execution_token:
        raise RuntimeError(
            "Cannot connect: EXECUTION_TOKEN not set. "
            "Either pass execution_token= explicitly or run inside a platform experiment kernel."
        )
    if not organization_id:
        raise RuntimeError(
            "Cannot connect: ORGANIZATION_ID not set. "
            "Either pass organization_id= explicitly or run inside a platform experiment kernel."
        )

    session = Session(
        egress_url=egress_url.rstrip("/"),
        execution_token=execution_token,
        organization_id=organization_id,
    )
    session.activate()
    _active_session = session
    return session


def disconnect() -> None:
    """Tear down the perceivo SDK notebook session."""
    global _active_session
    if _active_session is not None:
        _active_session.deactivate()
        _active_session = None
