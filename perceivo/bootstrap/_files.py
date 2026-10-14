"""Notebook implementation of FilesService.

Provides path-based file access (matching ``perceivo.files.write(path, content)``)
by resolving paths to folder hierarchies in org storage via the egress broker,
over the ID-based ``OrgFilesService``.
"""

from __future__ import annotations

import logging
from typing import Optional

from ._org_files import OrgFilesService

logger = logging.getLogger(__name__)


class FilesService:
    """Path-based file storage for notebooks, delegating to org storage."""

    def __init__(self, org_files: OrgFilesService):
        self._org_files = org_files
        # Cache resolved folder paths to avoid repeated lookups
        self._folder_cache: dict[str, str] = {}  # path -> entry_id

    async def _ensure_folder(self, folder_path: str) -> Optional[str]:
        """Create folder hierarchy if needed, return the final folder's entry_id."""
        if not folder_path:
            return None

        if folder_path in self._folder_cache:
            return self._folder_cache[folder_path]

        parts = [p for p in folder_path.split("/") if p]
        parent_id: Optional[str] = None

        for i, part in enumerate(parts):
            partial_path = "/".join(parts[: i + 1])
            if partial_path in self._folder_cache:
                parent_id = self._folder_cache[partial_path]
                continue

            items = await self._org_files.list(parent_id=parent_id)
            existing = next(
                (
                    item
                    for item in items
                    if item.get("name") == part and item.get("entry_type") == "folder"
                ),
                None,
            )

            if existing:
                parent_id = str(existing["id"])
            else:
                result = await self._org_files.create_folder(part, parent_id=parent_id)
                parent_id = str(result["id"])

            self._folder_cache[partial_path] = parent_id

        return parent_id

    async def _resolve_path(self, file_path: str) -> Optional[str]:
        """Walk the folder hierarchy to find a file's entry_id."""
        parts = [p for p in file_path.replace("\\", "/").split("/") if p]
        parent_id: Optional[str] = None

        for i, part in enumerate(parts):
            items = await self._org_files.list(parent_id=parent_id)
            match = next((item for item in items if item.get("name") == part), None)
            if not match:
                return None

            if i == len(parts) - 1:
                return str(match["id"])
            else:
                parent_id = str(match["id"])

        return None

    async def write(self, content: str | bytes, file_path: str) -> str:
        """Write content to a file path in org storage.

        Creates parent folders automatically if they don't exist.

        Args:
            content: File content (str or bytes).
            file_path: Path like ``"models/sentiment/model.pkl"``.

        Returns:
            The entry ID of the uploaded file.
        """
        parts = [p for p in file_path.replace("\\", "/").split("/") if p]
        file_name = parts[-1]
        dir_parts = parts[:-1]

        parent_id: Optional[str] = None
        if dir_parts:
            dir_path = "/".join(dir_parts)
            parent_id = await self._ensure_folder(dir_path)

        if isinstance(content, str):
            content = content.encode("utf-8")

        result = await self._org_files.write(file_name, content, parent_id=parent_id)
        # The write response nests the new id under ``entry``.
        entry_id = str((result.get("entry") or {}).get("id", ""))
        logger.info("perceivo.files.write: %s -> entry %s", file_path, entry_id)
        return entry_id

    async def read(self, file_path: str) -> str:
        """Read a file from org storage by path.

        Args:
            file_path: Path like ``"models/sentiment/model.pkl"``.

        Returns:
            The file content as a string.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        entry_id = await self._resolve_path(file_path)
        if not entry_id:
            raise FileNotFoundError(f"File not found in org storage: {file_path}")

        content = await self._org_files.read(entry_id)
        return content.decode("utf-8") if isinstance(content, bytes) else content
