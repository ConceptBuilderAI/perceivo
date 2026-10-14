"""Filesystem path containment, shared by every layer that joins an untrusted name.

Two subsystems build filesystem paths out of values they did not choose:

* the **local storage adapters**, which join orchestration- and seed-supplied
  keys (``documents/policy.txt``, an entry id round-tripped from a component)
  under a storage root, and
* the **bundler and validator**, which read the files an ``orchestration.yaml``
  names when a project is exported, published, or validated.

:func:`safe_path` confines an access to a fixed base directory: the candidate is
resolved to its canonical form and rejected unless it is the base itself or a
descendant of it. Absolute segments are rejected outright, so a supplied key can
never re-root the join.

An orchestration project may be shared, cloned or published as a template, so
``orchestration.yaml`` is untrusted input. Without this check a node declaring
``script: ../../../.ssh/id_rsa`` would pack that file into a bundle that
``publish()`` uploads, without any project code being executed.

Only graph-supplied paths go through :func:`resolve_within_project`. A path the
caller passes directly, such as ``perceivo export ./my_component.py``, is the
caller's own choice of file and is not constrained.
"""

from __future__ import annotations

import os
from pathlib import Path

from perceivo.types.errors import PerceivoError


class PathTraversalError(PerceivoError, ValueError):
    """Raised when a supplied path would escape the root it must stay under.

    Subclasses ``PerceivoError`` and ``ValueError``.
    """


def safe_path(root: str | os.PathLike[str], *parts: str | None) -> Path:
    """Resolve *parts* under *root*, rejecting any escape from the sandbox.

    Args:
        root: The root every access must stay within.
        parts: Untrusted path segments (entry ids, file names, prefixes).
            ``None`` and empty segments are skipped.

    Returns:
        The resolved absolute path, guaranteed to be *root* itself or a
        descendant of it.

    Raises:
        PathTraversalError: If a segment is absolute or the resolved path
            escapes *root*.
    """
    base = Path(root).resolve()
    candidate = base
    for part in parts:
        if not part:
            continue
        segment = os.fspath(part)
        if os.path.isabs(segment) or segment.startswith(("/", "\\")):
            raise PathTraversalError(f"Absolute path segments are not permitted: {segment!r}")
        candidate = candidate / segment

    resolved = candidate.resolve()

    # The resolved canonical path must stay within the sandbox root: it is the root
    # itself or starts with the root plus a separator. The static analyser accepts
    # this startswith form as a path-traversal sanitizer (python:S2083).
    base_str = str(base)
    resolved_str = str(resolved)
    if resolved_str != base_str and not resolved_str.startswith(base_str + os.sep):
        raise PathTraversalError(
            f"Path escapes the storage sandbox root {base_str!r}: {resolved_str!r}"
        )
    return resolved


def resolve_within_project(
    project: str | os.PathLike[str],
    relative: str | os.PathLike[str],
    *,
    field: str,
    node_id: str | None = None,
) -> Path:
    """Resolve a path an orchestration graph declared, or raise.

    Wraps :func:`safe_path` so the failure names the graph field and node that
    caused it.

    Args:
        project: The orchestration project directory the value is relative to.
        relative: The value taken from the graph (``script``, ``manifest``,
            ``project``).
        field: The graph field the value came from, for the error message.
        node_id: The node that declared it, when known.

    Returns:
        The resolved path, guaranteed to be inside *project*.

    Raises:
        PathTraversalError: If the value escapes *project* (via ``..``, an
            absolute path, or a symlink pointing out of the tree), or if it is
            empty, which would resolve to the project directory itself.
    """
    where = f"node {node_id!r} " if node_id else ""
    if not relative or not os.fspath(relative).strip():
        raise PathTraversalError(
            f"Refusing to read the project directory itself: {where}declares an empty {field}."
        )
    try:
        return safe_path(project, os.fspath(relative))
    except PathTraversalError as exc:
        raise PathTraversalError(
            f"Refusing to read outside the orchestration project: {where}declares "
            f"{field}={os.fspath(relative)!r}, which resolves outside "
            f"{Path(project).resolve()}. Paths in orchestration.yaml must point "
            f"inside the project directory, because exporting or publishing a "
            f"project reads the files its nodes name and packs their contents "
            f"into the bundle."
        ) from exc
