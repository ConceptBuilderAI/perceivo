"""The single root every local-runner write goes under: ``home()``.

**Resolution order.** The first that applies wins:

1. ``PERCEIVO_HOME`` in the environment;
2. a top-level ``root:`` in the ``perceivo.yaml`` that applies, resolved against
   that file's directory when it is relative;
3. ``<directory of that perceivo.yaml>/.perceivo``;
4. ``<current working directory>/.perceivo``.

"The ``perceivo.yaml`` that applies" is an explicit ``config_path`` when the
caller has one, otherwise the file :func:`~perceivo._internal.config_discovery.discover_config`
finds: ``PERCEIVO_CONFIG``, else the nearest ``perceivo.yaml`` at or above the
start directory. A run started from a subdirectory of a project, or from a
parent of it, therefore lands in the project's home.

**Layout.** ``runs/`` (run history), ``files/working/`` and ``files/org/`` (the
local file planes, unless ``files.*`` in ``perceivo.yaml`` names another
directory), ``registry/`` (installed components) and ``tmp/`` (per-session
scratch). Nothing here creates a directory on a read: :func:`home` and
:func:`subdir` only resolve paths, and each writer creates what it writes to.

**Scratch.** :func:`session_tmp` hands out ``<home>/tmp/<session>/`` and removes
it on exit, including when the body raises. A session directory a crashed
process left behind is removed the next time any session starts, once it is older
than :data:`STALE_AFTER_SECONDS`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from ..types.errors import ConfigurationError
from .config_discovery import discover_config

__all__ = ["HOME_ENV", "LAYOUT", "STALE_AFTER_SECONDS", "home", "session_tmp", "subdir"]

HOME_ENV = "PERCEIVO_HOME"

#: The directories a home holds, relative to it.
LAYOUT: tuple[str, ...] = ("runs", "files/working", "files/org", "registry", "tmp")

#: A session directory older than this is taken to be a crashed session's.
STALE_AFTER_SECONDS = 24 * 60 * 60

_PathLike = Union[str, "os.PathLike[str]"]


def _yaml_root(config_file: Path) -> Optional[str]:
    """Return the top-level ``root:`` of ``config_file``, or ``None`` when it has none."""
    try:
        raw: Any = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"perceivo.yaml:{config_file}: cannot be read: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("root") is None:
        return None
    root = raw["root"]
    if not isinstance(root, str) or not root.strip():
        raise ConfigurationError(f"perceivo.yaml:{config_file}: root must be a non-empty string")
    return root.strip()


def _applicable_config(
    start: Optional[_PathLike], config_path: Optional[_PathLike]
) -> Optional[Path]:
    if config_path is not None and Path(config_path).is_file():
        return Path(config_path).resolve()
    return discover_config(start)


def home(start: Optional[_PathLike] = None, *, config_path: Optional[_PathLike] = None) -> Path:
    """Resolve the local-runner root for ``start`` (default: the working directory).

    Resolves only; creates nothing.
    """
    named = os.environ.get(HOME_ENV)
    if named:
        return Path(named).expanduser().resolve()
    config_file = _applicable_config(start, config_path)
    if config_file is None:
        return (Path.cwd() / ".perceivo").resolve()
    root = _yaml_root(config_file)
    if root is None:
        return config_file.parent / ".perceivo"
    return (config_file.parent / Path(root).expanduser()).resolve()


def subdir(
    name: str, start: Optional[_PathLike] = None, *, config_path: Optional[_PathLike] = None
) -> Path:
    """Return ``home(start) / name`` for one of the :data:`LAYOUT` entries.

    Creates nothing.
    """
    if name not in LAYOUT:
        raise ConfigurationError(f"{name!r} is not part of the home layout {LAYOUT}")
    return home(start, config_path=config_path) / name


def _reap_stale(tmp_root: Path, now: float) -> None:
    """Remove session directories under ``tmp_root`` older than the stale limit."""
    for entry in tmp_root.iterdir():
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if entry.is_dir() and age > STALE_AFTER_SECONDS:
            shutil.rmtree(entry, ignore_errors=True)


@contextmanager
def session_tmp(prefix: str, start: Optional[_PathLike] = None) -> Iterator[Path]:
    """Yield a fresh ``<home>/tmp/<session>/`` directory, removed when the block exits."""
    tmp_root = subdir("tmp", start)
    tmp_root.mkdir(parents=True, exist_ok=True)
    _reap_stale(tmp_root, time.time())
    session = Path(tempfile.mkdtemp(prefix=prefix, dir=tmp_root))
    try:
        yield session
    finally:
        shutil.rmtree(session, ignore_errors=True)
