"""Resolve the category grid under ``managed_service``: one implementation, any category.

``perceivo.managed_service.<category>`` resolves any lowercase identifier
that is not withdrawn. There is no category list, because categories come
from a live catalog and grow without an SDK release, and resolution happens
at attribute access with no I/O. A typo such as ``managed_service.databse``
therefore resolves to an empty handle and fails at the next attribute.

The grid carries no verbs. ``connect`` exists only on ``database`` (see
:mod:`perceivo.managed_service.database`). Calling a verb on a generic
category raises ``AttributeError`` naming the verb.

Names in :data:`_WITHDRAWN` are refused with a message naming the platform
method that does not exist, rather than answered with a category handle.

Each handle is a module synthesised on first access, so an object segment is
never callable and a call site that forgets the verb fails immediately.
Attribute access and ``from perceivo.managed_service import cache`` both
work. ``import perceivo.managed_service.cache`` always raises
``ModuleNotFoundError``: handles are never registered in ``sys.modules``, so
the result never depends on what else the process touched first.
"""

from __future__ import annotations

import types

__all__ = ["resolve", "resolved"]

#: The verbs every catalog category carries. Empty: ``database`` builds its own
#: ``connect`` and is the only category with a verb.
_CATEGORY_VERBS: tuple[str, ...] = ()

#: ``{refused name: the platform method it would need}``. :func:`_withdrawn`
#: turns each into a message naming the missing method.
_WITHDRAWN: dict[str, str] = {
    "get_connection": "get_managed_service_connection",
    "get_status": "get_managed_service_status",
    "list": "list_managed_service_instances",
    "list_backups": "list_managed_service_backups",
    "list_instances": "list_managed_service_instances",
    "trigger_backup": "trigger_managed_service_backup",
}

#: The refused set, derived from :data:`_WITHDRAWN` so the two cannot disagree.
_RESERVED: frozenset[str] = frozenset(_WITHDRAWN)

_CATEGORY_DOC = """Managed {name} instances.

Resolved generically from the catalog category; carries no verbs. Use managed_service.database.connect for databases.
"""

#: Category names synthesised so far in this process, reported by ``dir()``.
_RESOLVED: set[str] = set()


def _withdrawn(package: str, name: str) -> str:
    """Return the ``AttributeError`` text for a refused name.

    Args:
        package: Name of the package the attribute was requested on.
        name: The refused name.

    Returns:
        A message naming the platform method that does not exist.
    """
    return (
        f"module {package!r} has no attribute {name!r}. This capability is "
        f"declared but not built: the method it forwards to, "
        f"{_WITHDRAWN[name]}, is not implemented in any current runtime."
    )


def _is_category_name(name: str) -> bool:
    """Return whether *name* could be a ``managed_service_catalog.category`` value.

    This is a shape test, not a membership test. It rejects the private probes
    (``__wrapped__``, ``_pytest_*``, ``_ipython_canary_*``) that the import
    system, pytest, ``inspect`` and ``copy`` send to a package ``__getattr__``.
    :data:`_WITHDRAWN` is checked by :func:`resolve` instead, because a withdrawn
    name and a malformed one get different messages.

    Args:
        name: The attribute name requested.

    Returns:
        True if *name* is shaped like a category.
    """
    return (
        bool(name)
        and not name.startswith("_")
        and name.isidentifier()
        and name == name.lower()
        and name.isascii()
    )


def resolve(package: types.ModuleType, name: str) -> types.ModuleType:
    """Return the handle for catalog category *name*, synthesising it once.

    Args:
        package: The ``perceivo.managed_service`` package, so the handle can be
            cached onto it and named as its child.
        name: The category segment as written at the call site.

    Returns:
        An uncallable module carrying :data:`_CATEGORY_VERBS`, cached on
        *package* so the attribute and ``from`` spellings resolve to the same
        object. Not registered in ``sys.modules``, so ``import
        perceivo.managed_service.cache`` always fails.

    Raises:
        AttributeError: *name* is withdrawn (message from :func:`_withdrawn`)
            or is not shaped like a catalog category. Raised rather than
            returned as a broken handle, so ``hasattr`` stays accurate for the
            private probes described in :func:`_is_category_name`.
    """
    if name in _RESERVED:
        raise AttributeError(_withdrawn(package.__name__, name))
    if not _is_category_name(name):
        raise AttributeError(f"module {package.__name__!r} has no attribute {name!r}")

    handle = types.ModuleType(f"{package.__name__}.{name}", _CATEGORY_DOC.format(name=name))
    handle.__all__ = sorted(_CATEGORY_VERBS)  # type: ignore[attr-defined]
    handle.__package__ = package.__name__
    for verb in _CATEGORY_VERBS:
        setattr(handle, verb, globals()[verb])
    _RESOLVED.add(name)
    setattr(package, name, handle)
    return handle


def resolved() -> frozenset[str]:
    """Return the category names resolved so far in this process.

    Used by ``perceivo.managed_service.__dir__`` to report what has been asked
    for without claiming to know the catalog.

    Returns:
        The resolved category names.
    """
    return frozenset(_RESOLVED)
