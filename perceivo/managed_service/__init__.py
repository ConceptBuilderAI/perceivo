"""Azure services the platform reaches on your behalf.

Two hand-written objects, and a generic grid for the rest::

    perceivo.managed_service.ai          managed AI (chat, embeddings, ...)
    perceivo.managed_service.database    managed databases
    perceivo.managed_service.<category>  resolved from the live catalog

Each object is a module, so it cannot be called: a call site that forgets
``.invoke`` or ``.connect`` fails immediately.

- ``ai``: ``invoke`` (chat, embedding, groundedness, content safety) and
  ``models``.
- ``database.connect(id).query(sql)``: ``connect`` is a lazy handle and needs
  no credential resolution.
- ``<category>``: any other catalog category resolves to a handle carrying no
  verbs.

This package is imported eagerly by ``perceivo``, so neither module may import
``perceivo.local`` or ``perceivo.config`` at module scope;
:mod:`perceivo.managed_service.ai` imports them inside functions on the
outside-a-run path only.
"""

from __future__ import annotations

import sys
import types

from . import ai, database
from ._catalog import resolve as _resolve_category
from ._catalog import resolved as _resolved_categories

#: Catalog categories are resolved from the live catalog, so they are not listed.
#: ``list`` is withdrawn; ``_catalog._WITHDRAWN`` keeps ``__getattr__`` refusing it.
__all__ = ["ai", "database"]


def __getattr__(name: str) -> types.ModuleType:
    """Resolve ``perceivo.managed_service.<category>`` from the live catalog.

    Only reached for names not already bound, so ``ai`` and ``database`` never
    arrive here. ``list`` is withdrawn and :func:`._catalog.resolve` refuses it
    by name.
    """
    return _resolve_category(sys.modules[__name__], name)


def __dir__() -> list[str]:
    """Return ``__all__`` plus the categories resolved so far.

    An unresolved category is still reachable; it simply has not been asked for
    yet. Built from ``__all__`` rather than ``globals()`` so imports are not
    reported as surface.
    """
    return sorted(set(__all__) | _resolved_categories())
