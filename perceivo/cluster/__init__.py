"""In-cluster services.

    perceivo.cluster.<service>.connect()

The services are ``elasticsearch``, ``mcp``, ``milvus`` and ``redis``::

    redis = await perceivo.cluster.redis.connect()
    mcp = await perceivo.cluster.mcp.connect()
    tools = await mcp.list_tools()

Each handle is a module and cannot be called, so a call site that forgets
``.connect`` fails immediately. The service set is derived from
:mod:`perceivo.cluster._backend` at import.
"""

from __future__ import annotations

import sys
import types

from ._registry import SERVICES as _SERVICES
from ._registry import resolve as _resolve_service

#: Derived from the backend; members are resolved by :func:`__getattr__` on
#: first access.
__all__ = sorted(_SERVICES)


def __getattr__(name: str) -> types.ModuleType:
    """Resolve ``perceivo.cluster.<service>`` from the backend's export contract.

    Only reached for names not already bound; a resolved handle is cached on the
    package.
    """
    return _resolve_service(sys.modules[__name__], name)


def __dir__() -> list[str]:
    """Return every service this SDK can connect to, resolved or not."""
    return list(__all__)
