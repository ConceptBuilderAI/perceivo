"""Local state that a copy of one project into another never carries.

A project's ``.env`` files hold its author's secrets and ``.perceivo/`` holds
its local run state, including run inputs. Neither is part of the graph. Every
``copytree`` that lays a project down inside another (nested children on save,
a nested project carried to a new destination, and ``import-bundle --members``)
passes :func:`ignore_local_state`.
"""

from __future__ import annotations

__all__ = ["LOCAL_STATE_NAMES", "ignore_local_state"]

#: Exact directory-entry names dropped; ``.env.*`` variants are dropped too.
LOCAL_STATE_NAMES = frozenset({".env", ".perceivo"})


def ignore_local_state(_directory: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ``ignore=`` hook dropping ``.env``, ``.env.*``, ``.perceivo``."""
    return {n for n in names if n in LOCAL_STATE_NAMES or n.startswith(".env.")}
