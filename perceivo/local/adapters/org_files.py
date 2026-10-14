"""Local org files adapter backed by a configurable local directory."""

from __future__ import annotations

import builtins
import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

# Aliased private: this module has no ``__all__``, so a bare name would read as
# public surface.
from ..._internal.mime import guess_text_mime as _guess_text_mime
from ._safe_path import safe_path


class LocalOrgFilesService:
    """Organization file storage backed by a local directory.

    Files are stored flat with path-based entry IDs to simulate the hosted
    ID-based access pattern. Return shapes match the public ``perceivo.files`` API:

      - ``write``/``write_text`` return ``{entry, version}`` (an
        :class:`UploadResponse`-shaped dict). The file ID is at
        ``result["entry"]["id"]``.
      - ``list``/``find`` (and the single-entry mutators ``create_folder`` /
        ``rename`` / ``move``) return entries carrying ``entry_type`` and
        ``size_bytes`` (not ``type`` / ``size``).
    """

    def __init__(self, org_dir: str = "./org_files", trash_dir: Optional[str] = None):
        # Resolve the root up front so it matches the canonical (resolved) paths
        # produced by ``safe_path``; keeps ``relative_to`` in :meth:`_entry` exact.
        self._root = Path(org_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # Outside the org root, so a trashed item is neither listed nor found.
        self._trash = Path(trash_dir).resolve() if trash_dir else self._root.parent / "trash"

    def _resolve(self, entry_id: str) -> Path:
        """Resolve an entry ID to a path, treating ID as a sandboxed relative path.

        The entry id is orchestration-supplied and therefore untrusted; it is
        confined to the storage root so a crafted id (``../secrets``, an absolute
        path) cannot escape the sandbox (path-traversal hardening).
        """
        return safe_path(self._root, entry_id)

    def _entry(self, item: Path, mime_type: Optional[str] = None) -> dict[str, Any]:
        """Build a ``FileEntryRead``-shaped dict for a path on disk.

        Mirrors the production ``FileEntryRead`` contract: the fields a
        component reads via the public ``perceivo.files`` docstrings
        (``id``, ``name``, ``path``, ``entry_type``, ``parent_id``,
        ``mime_type``, ``size_bytes``, ``blob_name``, ``created_at``,
        ``updated_at``). ``id`` remains the space-relative path so it can be
        round-tripped back through :meth:`read` / :meth:`_resolve`.
        """
        rel = item.relative_to(self._root)
        is_file = item.is_file()
        parent_rel = str(item.parent.relative_to(self._root))
        parent_id = None if parent_rel == "." else parent_rel
        stat = item.stat()
        created_at = datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        return {
            "id": str(rel),
            "name": item.name,
            "path": "/" + rel.as_posix(),
            "entry_type": "file" if is_file else "folder",
            "parent_id": parent_id,
            "mime_type": mime_type if is_file else None,
            "size_bytes": stat.st_size if is_file else None,
            "blob_name": None,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    async def list(
        self,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        entry_type: Optional[str] = None,
    ) -> builtins.list[dict[str, Any]]:
        target_dir = safe_path(self._root, parent_id)
        # ``exists()`` is True for a file too, but ``iterdir()`` raises on one;
        # listing a non-directory path yields no children rather than crashing.
        if not target_dir.is_dir():
            return []

        entries = []
        for item in sorted(target_dir.iterdir()):
            entry = self._entry(item)
            if entry_type and entry["entry_type"] != entry_type:
                continue
            entries.append(entry)
        return entries

    async def read(self, entry_id: str) -> bytes:
        path = self._resolve(entry_id)
        if not path.exists():
            raise FileNotFoundError(f"Org file not found: {path}")
        return path.read_bytes()

    async def read_text(self, entry_id: str, encoding: str = "utf-8") -> str:
        data = await self.read(entry_id)
        return data.decode(encoding)

    async def write(
        self,
        file_name: str,
        content: str | bytes,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> dict[str, Any]:
        target = safe_path(self._root, parent_id, file_name)
        target.parent.mkdir(parents=True, exist_ok=True)

        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)

        if mime_type is None:
            # The rule ``write_text`` applies: the name's suffix, else text/plain.
            mime_type = _guess_text_mime(file_name)
        entry = self._entry(target, mime_type=mime_type)
        # Content MD5 for integrity, matching hosted storage; not a security context.
        checksum_md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        version = {
            "id": str(uuid.uuid4()),
            "file_entry_id": entry["id"],
            "version_number": 1,
            "blob_version_id": None,
            "size_bytes": len(data),
            "checksum_md5": checksum_md5,
            "comment": None,
            "is_current": True,
            "created_at": entry["created_at"],
        }
        return {"entry": entry, "version": version}

    async def write_text(
        self,
        file_name: str,
        content: str,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        # Same resolver as the in-cluster client, so a local run records the
        # media type the host would have recorded instead of ``None``.
        return await self.write(
            file_name=file_name,
            content=content.encode(encoding),
            parent_id=parent_id,
            space_id=space_id,
            mime_type=_guess_text_mime(file_name),
        )

    async def create_folder(
        self,
        folder_name: str,
        parent_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ) -> dict[str, Any]:
        folder = safe_path(self._root, parent_id, folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        return self._entry(folder)

    async def rename(self, entry_id: str, new_name: str) -> dict[str, Any]:
        old_path = self._resolve(entry_id)
        # ``new_name`` is untrusted; confine it to the existing parent directory
        # (already within the root) so a rename cannot relocate outside the sandbox.
        new_path = safe_path(old_path.parent, new_name)
        old_path.rename(new_path)
        return self._entry(new_path)

    async def delete(self, entry_id: str, permanent: bool = False) -> None:
        """Delete an entry, or move it to the trash directory.

        Without ``permanent`` the entry moves to a new folder under the trash
        directory, keeping its path below the org root:
        ``<trash>/<random hex>/<entry id>``.
        """
        path = self._resolve(entry_id)
        if not path.exists():
            return
        if not permanent:
            target = self._trash / uuid.uuid4().hex / path.relative_to(self._root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    async def move(self, entry_id: str, new_parent_id: str) -> dict[str, Any]:
        old_path = self._resolve(entry_id)
        new_path = safe_path(self._root, new_parent_id, old_path.name)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        return self._entry(new_path)

    async def find(
        self,
        name: str,
        space_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Find the first entry whose filename matches ``name`` (recursive)."""
        if not self._root.exists():
            return None
        for item in self._root.rglob(name):
            return self._entry(item)
        return None
