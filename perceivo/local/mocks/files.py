"""Mock org file storage: seeded, forgiving local file storage for runs.

Extends :class:`LocalOrgFilesService` with two behaviours: seed files are
written into the storage root before a run, and a read or find for a missing
path returns default content (or a synthesized entry) instead of raising
``FileNotFoundError``.
"""

from __future__ import annotations

from typing import Any, Optional

from ..adapters._safe_path import PathTraversalError, safe_path
from ..adapters.org_files import LocalOrgFilesService

_DEFAULT_CONTENT = "Mock file content (perceivo local mock)."


class MockOrgFilesService(LocalOrgFilesService):
    """Seeded org file storage that never hard-fails a missing read."""

    def __init__(self, org_dir: str, seed: dict[str, Any], trash_dir: Optional[str] = None):
        super().__init__(org_dir=org_dir, trash_dir=trash_dir)
        self._seed = seed or {}
        self._seed_files()

    def _seed_files(self) -> None:
        """Write each seeded ``path: content`` entry into the storage root."""
        for rel_path, content in self._seed.items():
            if not isinstance(rel_path, str):
                continue
            try:
                # Seed keys are developer-supplied and untrusted; confine every seeded path to
                # the storage root.
                target = safe_path(self._root, rel_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                data = content if isinstance(content, (bytes, bytearray)) else str(content)
                # Write bytes: text mode would translate newlines to os.linesep on Windows.
                if isinstance(data, str):
                    data = data.encode("utf-8")
                target.write_bytes(data)
            except (OSError, PathTraversalError):
                # A path-traversal key or a stale on-disk conflict is skipped rather than
                # aborting the run.
                continue

    async def read(self, entry_id: str) -> bytes:
        path = self._resolve(entry_id)
        if not path.exists():
            return _DEFAULT_CONTENT.encode("utf-8")
        return path.read_bytes()

    async def read_text(self, entry_id: str, encoding: str = "utf-8") -> str:
        data = await self.read(entry_id)
        return data.decode(encoding)
