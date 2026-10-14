"""Where the local runner's configured paths resolve.

**Relative to the file.** A relative ``files.working_dir``, ``files.org_dir`` or
``secrets.dotenv_path`` written in ``perceivo.yaml`` is relative to that file,
so a run started from any directory resolves it to the same place.
``secrets.dotenv_path`` defaults to the ``.env`` beside the file, which is where
``perceivo init`` and ``perceivo new orchestration`` write it.

**Under the root.** An empty ``files.*`` means the local-runner root:
``<root>/files/working`` and ``<root>/files/org``. An org-files delete without
``permanent`` moves the item to ``<root>/files/trash``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .home import home

__all__ = ["FILE_RELATIVE_KEYS", "file_roots", "resolve_file_relative", "trash_root"]

#: ``(section, key)`` pairs whose values are paths.
FILE_RELATIVE_KEYS = (
    ("files", "working_dir"),
    ("files", "org_dir"),
    ("secrets", "dotenv_path"),
)


def resolve_file_relative(
    raw: dict[str, Any], base_dir: Path, default_dotenv: str
) -> dict[str, Any]:
    """Resolve the relative paths in a parsed ``perceivo.yaml`` against ``base_dir``."""
    if raw.get("secrets") is None:
        raw["secrets"] = {}
    secrets = raw["secrets"]
    if isinstance(secrets, dict):
        secrets.setdefault(FILE_RELATIVE_KEYS[2][1], default_dotenv)
    for section, key in FILE_RELATIVE_KEYS:
        block = raw.get(section)
        value = block.get(key) if isinstance(block, dict) else None
        if isinstance(value, str) and value.strip() and not Path(value).is_absolute():
            block[key] = str((base_dir / value).resolve())  # type: ignore[index]
    return raw


def file_roots(config: Any) -> tuple[str, str]:
    """Return the working and org file roots: ``files.*`` when set, else under the root."""
    root = Path(config.root) if config.root else home()
    working = config.files.working_dir or str(root / "files" / "working")
    org = config.files.org_dir or str(root / "files" / "org")
    return working, org


def trash_root(config: Any) -> str:
    """Return where a local org-files delete without ``permanent`` moves the item."""
    root = Path(config.root) if config.root else home()
    return str(root / "files" / "trash")
