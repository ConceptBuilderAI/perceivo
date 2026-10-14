"""Notebook implementation of OrgFilesService.

Every operation, including file content I/O, is routed through the egress
broker, which validates the caller's EXECUTION_TOKEN, enforces the
``files:read`` / ``files:write`` capabilities, rate-limits, audits, and
proxies to the platform.

Workload code holds no storage credential and opens no connection to blob
storage. A direct-to-blob transfer from a workload pod is denied by the mesh,
so the SDK must not attempt one.

Implements the same interface as the orchestration runtime's OrgFilesService
so that ``perceivo.files.*`` functions work transparently.
"""

from __future__ import annotations

import builtins
import logging
from typing import Any, Optional

import httpx

from .._internal.mime import guess_text_mime

logger = logging.getLogger(__name__)


_FRIENDLY_ERRORS = {
    502: "Storage service is temporarily unreachable. Please try again.",
    503: "Storage service is temporarily unavailable. Please try again in a moment.",
    504: "Storage service request timed out. Please try again.",
}


def _check_response(resp: httpx.Response, operation: str = "request") -> None:
    """Check HTTP response and raise user-friendly errors."""
    if resp.status_code < 400:
        return
    friendly = _FRIENDLY_ERRORS.get(resp.status_code)
    if friendly:
        logger.error(
            "org_files %s failed: status=%d url=%s",
            operation,
            resp.status_code,
            str(resp.url),
        )
        raise RuntimeError(friendly)
    if resp.status_code == 404:
        raise FileNotFoundError("File or folder not found.")
    # Other errors: log details but show a clean message to the user.
    logger.error(
        "org_files %s failed: status=%d body=%s",
        operation,
        resp.status_code,
        resp.text[:500],
    )
    raise RuntimeError(f"File operation failed (HTTP {resp.status_code}). Check logs for details.")


class OrgFilesService:
    """Organization file storage routed through the egress broker."""

    def __init__(
        self,
        egress_url: str,
        execution_token: str,
        organization_id: str,
    ):
        self._egress_url = egress_url
        self._organization_id = organization_id
        self._default_space_id: Optional[str] = None
        self._client = httpx.AsyncClient(
            timeout=300.0,
            headers={"Authorization": f"Bearer {execution_token}"},
        )

    async def _get_space_id(self, space_id: Optional[str] = None) -> str:
        """Resolve storage space ID, fetching default if needed."""
        if space_id:
            return space_id
        if self._default_space_id:
            return self._default_space_id

        # List spaces via egress broker and pick the first one
        resp = await self._client.get(
            f"{self._egress_url}/internal/org-files/spaces",
            params={"organization_id": self._organization_id},
        )
        _check_response(resp, "list spaces")
        spaces = resp.json()
        if not spaces:
            raise RuntimeError(f"No storage spaces found for organization {self._organization_id}")
        self._default_space_id = str(spaces[0]["id"])
        return self._default_space_id

    async def list(
        self,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        entry_type: Optional[str] = None,
    ) -> builtins.list[dict[str, Any]]:
        """List files and folders in organization storage."""
        sid = await self._get_space_id(space_id)
        params: dict[str, Any] = {"page_size": 100}
        if parent_id:
            params["parent_id"] = parent_id

        resp = await self._client.get(
            f"{self._egress_url}/internal/org-files/spaces/{sid}/contents",
            params=params,
        )
        _check_response(resp, "list files")
        data = resp.json()
        items: builtins.list[dict[str, Any]] = data.get("items", [])

        if entry_type:
            items = [i for i in items if i.get("entry_type") == entry_type]

        return items

    async def read(self, entry_id: str) -> bytes:
        """Download a file's content by entry ID.

        The download is routed through the egress broker, which streams the body
        with no size cap. Workload pods cannot reach blob storage directly, so
        there is no SAS path and no fallback.

        Args:
            entry_id: ID of the file entry to download.

        Returns:
            The raw file content.
        """
        resp = await self._client.get(
            f"{self._egress_url}/internal/org-files/entries/{entry_id}/download",
        )
        _check_response(resp, "download file")
        return resp.content

    async def read_text(self, entry_id: str, encoding: str = "utf-8") -> str:
        """Download a file's content as text."""
        content = await self.read(entry_id)
        return content.decode(encoding)

    async def write(
        self,
        file_name: str,
        content: bytes,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Upload a file to organization storage.

        The upload is a single multipart POST through the egress broker. The
        server writes the blob and registers the entry in one transaction, so a
        failure leaves no orphaned entry.

        Args:
            file_name: Name of the file to create.
            content: Raw file content.
            parent_id: Folder entry to upload into. Defaults to the space root.
            space_id: Storage space to upload into. Defaults to the organization's
                first storage space.
            mime_type: MIME type of the content. If omitted, the type comes from
                the file name's suffix, else ``text/plain``, as for :meth:`write_text`.

        Returns:
            The upload response (``{"entry": ..., "version": ...}``) unchanged.
        """
        # The host stores whatever type the part declares, so an omitted type is
        # resolved here rather than sent as application/octet-stream.
        if mime_type is None:
            mime_type = guess_text_mime(file_name)
        sid = await self._get_space_id(space_id)
        # parent_id is a form field on the upload route, not a query parameter; sent
        # as a query parameter the file would land in the space root.
        data: dict[str, Any] = {}
        if parent_id:
            data["parent_id"] = str(parent_id)

        resp = await self._client.post(
            f"{self._egress_url}/internal/org-files/spaces/{sid}/upload",
            files={"file": (file_name, content, mime_type)},
            data=data,
        )
        _check_response(resp, "upload file")
        result: dict[str, Any] = resp.json()
        logger.info(
            "Uploaded %s (%d bytes) -> entry %s",
            file_name,
            len(content),
            result.get("entry", {}).get("id", ""),
        )
        return result

    async def write_text(
        self,
        file_name: str,
        content: str,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Upload a text file to organization storage.

        The media type comes from the file name (``_internal.mime``), not from
        a fixed ``text/plain``. The host applies no allowlist and believes what
        the multipart part declares, so a fixed constant would override the
        host's own ``mimetypes.guess_type`` fallback and store every
        ``.json``, ``.csv`` and ``.md`` file as plain text. A suffixless name,
        or any name that does not resolve, falls back to ``text/plain``.
        """
        return await self.write(
            file_name=file_name,
            content=content.encode(encoding),
            parent_id=parent_id,
            space_id=space_id,
            mime_type=guess_text_mime(file_name),
        )

    async def create_folder(
        self,
        folder_name: str,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a folder in organization storage."""
        sid = await self._get_space_id(space_id)
        resp = await self._client.post(
            f"{self._egress_url}/internal/org-files/spaces/{sid}/folders",
            json={"name": folder_name, "parent_id": parent_id},
        )
        _check_response(resp, "create folder")
        result: dict[str, Any] = resp.json()
        return result

    async def rename(self, entry_id: str, new_name: str) -> dict[str, Any]:
        """Rename a file or folder."""
        resp = await self._client.patch(
            f"{self._egress_url}/internal/org-files/entries/{entry_id}",
            json={"name": new_name},
        )
        _check_response(resp, "rename")
        result: dict[str, Any] = resp.json()
        return result

    async def delete(self, entry_id: str, permanent: bool = False) -> None:
        """Delete a file or folder."""
        resp = await self._client.delete(
            f"{self._egress_url}/internal/org-files/entries/{entry_id}",
            params={"permanent": permanent},
        )
        _check_response(resp, "delete")

    async def move(self, entry_id: str, new_parent_id: Optional[str]) -> dict[str, Any]:
        """Move a file or folder."""
        resp = await self._client.post(
            f"{self._egress_url}/internal/org-files/entries/{entry_id}/move",
            json={"new_parent_id": new_parent_id},
        )
        _check_response(resp, "move")
        result: dict[str, Any] = resp.json()
        return result
