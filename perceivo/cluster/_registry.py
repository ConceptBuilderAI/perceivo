"""The service grid under ``cluster``, derived from the backend.

:data:`SERVICES` is read from :mod:`perceivo.cluster._backend`'s ``__all__``
at import, so a service the backend exports is served as
``perceivo.cluster.<service>`` with no edit here. The set is closed: each
service needs its own platform accessor, so an unknown name raises
``AttributeError`` rather than returning a handle that forwards to nothing.

Reach a service by attribute access, ``perceivo.cluster.milvus.connect``,
or ``from perceivo.cluster import milvus``. ``import perceivo.cluster.milvus``
raises ``ModuleNotFoundError``, because the handles are synthesised modules
with no file.

Each handle carries one verb, ``connect``.
"""

from __future__ import annotations

import types

from . import _backend

__all__ = ["SERVICES", "resolve"]

#: The verbs every in-cluster service handle carries.
_SERVICE_VERBS = ("connect",)

#: ``{service name: the accessor that connects to it}``, derived from the
#: backend's ``__all__``: ``get_milvus`` is served as ``milvus``.
SERVICES: dict[str, object] = {
    name.removeprefix("get_"): getattr(_backend, name)
    for name in _backend.__all__
    if name.startswith("get_")
}

_SERVICE_DOC = """The org's in-cluster {name}.

The handle is a module and is not callable; connect through it::

    client = await perceivo.cluster.{name}.connect()

Only ``connect`` is offered.
"""


def resolve(package: types.ModuleType, name: str) -> types.ModuleType:
    """Return the handle for in-cluster service *name*, synthesising it once.

    Args:
        package: The ``perceivo.cluster`` package, so the handle can be cached
            onto it and named as its child.
        name: The service segment as written at the call site.

    Returns:
        An uncallable module carrying :data:`_SERVICE_VERBS`, cached on
        *package* so the second access returns the same object.

    Raises:
        AttributeError: *name* is not a service the backend can reach.
    """
    if name not in SERVICES:
        raise AttributeError(_no_service(package.__name__, name))

    handle = types.ModuleType(f"{package.__name__}.{name}", _SERVICE_DOC.format(name=name))
    handle.__all__ = sorted(_SERVICE_VERBS)  # type: ignore[attr-defined]
    handle.__package__ = package.__name__
    # The backend's own function object: ``cluster.milvus.connect is
    # _backend.get_milvus`` holds.
    handle.connect = SERVICES[name]  # type: ignore[attr-defined]
    setattr(package, name, handle)
    return handle


def _no_service(package_name: str, name: str) -> str:
    """Return the ``AttributeError`` text for a name that is not a reachable service."""
    return (
        f"module {package_name!r} has no attribute {name!r}. The in-cluster "
        f"services this SDK can reach are {', '.join(sorted(SERVICES))}; "
        f"each is reached as perceivo.cluster.<service>.connect()."
    )
