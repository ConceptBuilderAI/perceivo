"""Typed adapters that refuse file and secret access in disabled mode."""

from __future__ import annotations

import builtins
from typing import Any, Never, Optional, Protocol

from ...config import NO_LOCAL_BACKEND
from ...types.errors import ConfigurationError


def disabled_service_error(service: str) -> ConfigurationError:
    """Build the shared, actionable error for a disabled local service."""
    recovery = "'mock'" if service in NO_LOCAL_BACKEND else "'mock' or 'local'"
    return ConfigurationError(
        f"Local service {service!r} is disabled. Set mocks.modes.{service} to "
        f"{recovery} in perceivo.yaml before starting the run."
    )


class _ModeResolver(Protocol):
    def mode(self, service: str) -> str: ...


def require_enabled(mocks: _ModeResolver, service: str) -> None:
    """Refuse a disabled service before an adapter or mock is touched."""
    if mocks.mode(service) == "disabled":
        raise disabled_service_error(service)


class _DisabledService:
    service: str

    def _raise(self) -> Never:
        raise disabled_service_error(self.service)


class DisabledFilesService(_DisabledService):
    """Signature-compatible working-file adapter for disabled mode."""

    service = "files"

    async def read(self, file_path: str) -> str:
        self._raise()

    async def write(self, content: str | bytes, file_path: str) -> str:
        self._raise()

    async def list_blobs(
        self, container: str, prefix: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        self._raise()

    async def read_blob(self, container: str, path: str) -> str:
        self._raise()


class DisabledOrgFilesService(_DisabledService):
    """Signature-compatible organization-file adapter for disabled mode."""

    service = "files"

    async def list(
        self,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        entry_type: Optional[str] = None,
    ) -> builtins.list[dict[str, Any]]:
        self._raise()

    async def read(self, entry_id: str) -> bytes:
        self._raise()

    async def read_text(self, entry_id: str, encoding: str = "utf-8") -> str:
        self._raise()

    async def write(
        self,
        file_name: str,
        content: str | bytes,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> dict[str, Any]:
        self._raise()

    async def write_text(
        self,
        file_name: str,
        content: str,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        self._raise()

    async def create_folder(
        self,
        folder_name: str,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ) -> dict[str, Any]:
        self._raise()

    async def rename(self, entry_id: str, new_name: str) -> dict[str, Any]:
        self._raise()

    async def delete(self, entry_id: str, permanent: bool = False) -> None:
        self._raise()

    async def move(self, entry_id: str, new_parent_id: str) -> dict[str, Any]:
        self._raise()

    async def find(self, name: str, space_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        self._raise()


class DisabledSecretsService(_DisabledService):
    """Secret adapter that performs no environment or dotenv reads."""

    service = "secrets"

    async def fetch_secret_value(self, secret_id: Optional[str]) -> Optional[str]:
        self._raise()

    def get_all_keys(self) -> list[Any]:
        self._raise()
