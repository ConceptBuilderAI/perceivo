"""Local file storage adapter backed by the local filesystem."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._safe_path import safe_path


class LocalFilesService:
    """File storage service backed by a local directory."""

    def __init__(self, working_dir: str = "./working_files"):
        # Resolve up front so paths compared against the root (and returned to
        # callers) are canonical and consistent with ``safe_path``.
        self._root = Path(working_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def read(self, file_path: str) -> str:
        # ``file_path`` is component-supplied and untrusted; confine it to the
        # working directory so it cannot escape the sandbox (path-traversal).
        target = safe_path(self._root, file_path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {target}")
        return target.read_text(encoding="utf-8")

    async def write(self, content: str | bytes, file_path: str) -> str:
        target = safe_path(self._root, file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return str(target.resolve())

    def _container_root(self, container: str) -> Path:
        """Resolve the reserved working container to the configured root."""
        if container == "working":
            return self._root
        return safe_path(self._root, container)

    async def list_blobs(
        self, container: str, prefix: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List files matching a prefix pattern in the working directory."""
        results: list[dict[str, Any]] = []
        search_dir = self._container_root(container)
        if not search_dir.exists():
            return results

        for item in sorted(search_dir.rglob("*")):
            relative = item.relative_to(search_dir).as_posix()
            if item.is_file() and relative.startswith(prefix):
                stat = item.stat()
                results.append(
                    {
                        "name": relative,
                        "size": stat.st_size,
                        # ISO-8601 string, matching hosted storage.
                        "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    }
                )
                if len(results) >= limit:
                    break
        return results

    async def read_blob(self, container: str, path: str) -> str:
        target = safe_path(self._container_root(container), path)
        if not target.exists():
            raise FileNotFoundError(f"Blob not found: {target}")
        return target.read_text(encoding="utf-8")
