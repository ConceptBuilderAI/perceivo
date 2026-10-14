"""Control and inspect how a local run is served.

Every platform call a local run makes is served by a mock, by a real backend you
configured, or not at all. This module says which, changes it, and reports what
happened::

    import perceivo.local as local

    local.mode("redis")                # "mock" | "local" | "disabled"
    local.set_mode("redis", "local")   # use the real backend in perceivo.yaml

    page = local.trace()               # Page[MockCall]: every call the run made
    for call in page:
        print(call.service, call.operation, call.mode)

Not every service supports every mode. ``llm``, ``redis``, ``milvus``,
``elasticsearch`` and ``database`` can use a real backend; ``files`` and
``secrets`` have a local adapter instead, and ``local`` means that adapter.
``mcp`` and ``chat`` have neither, so they are either mocked or disabled.
Setting an unsupported mode raises :class:`ValueError`, and ``perceivo.yaml``
naming one is refused at load.

Every local run emits its trace (``ExecutionResult.mock_calls``, also printed by
the run CLI). :func:`trace` reads it from inside a component, or from the same
process afterwards.
"""

from __future__ import annotations

from typing import Any

from ..types.errors import ContextError
from ..types.pagination import Page
from . import _modes
from .mocks.trace import MockCall, last_run_calls

__all__ = ["mode", "set_mode", "trace"]


def _active_ext() -> Any | None:
    """The local extension manager currently on the SDK stack, or ``None``.

    ``_mocks`` is the discriminator rather than an isinstance check because the
    stack also carries the cloud manager and the managed-service standalone ext,
    neither of which serves a mode or a trace.
    """
    from .._internal.context import _ExecutionContext

    if not _ExecutionContext.is_active():
        return None
    ext = _ExecutionContext.get_ext()
    return ext if hasattr(ext, "_mocks") else None


def mode(service: str) -> str:
    """How *service* is being served: ``"mock"``, ``"local"`` or ``"disabled"``.

    Inside a run this is the live answer for that run. Outside one it is what the
    next run would resolve to, from ``perceivo.yaml`` plus any :func:`set_mode`
    already applied.

    Raises:
        ValueError: If *service* is not one the SDK can mock.
    """
    _modes.check_service(service)

    ext = _active_ext()
    if ext is not None:
        return _modes.to_declared(ext._mocks.mode(service))

    from ..config import load

    config = load()
    _modes.apply_overrides(config)
    return _modes.to_declared(config.service_mode(service))


def set_mode(service: str, mode: str) -> None:
    """Serve *service* in *mode*: ``"mock"``, ``"local"`` or ``"disabled"``.

    Call this before a run starts. It does not write ``perceivo.yaml``; it is a
    process-level override.

    Args:
        service: The service to configure.
        mode: The mode to serve it in.

    Raises:
        ValueError: If *service* is not one the SDK can mock, or *mode* is not
            one that service can be in (``mcp`` and ``chat`` have no local
            backend).
        ContextError: If a local run is active. Its adapters were selected at
            construction and cannot be changed atomically.
    """
    internal = _modes.to_internal(service, mode)
    ext = _active_ext()
    if ext is not None:
        raise ContextError(
            "perceivo.local.set_mode() cannot change an active local run. "
            "Set the mode before starting the next run."
        )
    _modes.set_override(service, internal)


def trace() -> Page[MockCall]:
    """Every platform call served so far, in order.

    Inside a run: that run's calls up to this point. Outside one: the calls of
    the last local run in this process, which is empty if there has not been
    one. One page, because a local run's trace is held whole in memory.
    """
    ext = _active_ext()
    calls = ext._mocks.trace.calls if ext is not None else last_run_calls()
    return Page(data=calls, total=len(calls))
