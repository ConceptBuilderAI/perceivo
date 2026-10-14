"""Service-mode vocabulary shared by ``perceivo.local`` and the local extension manager.

The public API has three modes, ``"mock"``, ``"local"`` and ``"disabled"``,
while the configuration stores a real LLM backend as ``"byo-key"``. This module
translates in both directions.

``DECLARED_MODES`` and ``NO_LOCAL_BACKEND`` are imported from
:mod:`perceivo.config`, so ``perceivo.yaml`` and :func:`perceivo.local.set_mode`
accept and reject the same modes.

It also holds pending overrides. :func:`perceivo.local.set_mode` is usually
called before a run starts, when no extension manager exists; the override is
held here and applied to every
:class:`~perceivo.local.context.LocalExtensionManager` built afterwards.
"""

from __future__ import annotations

from typing import Any

from ..config import DECLARED_MODES, MOCKABLE_SERVICES, NO_LOCAL_BACKEND

__all__ = [
    "DECLARED_MODES",
    "NO_LOCAL_BACKEND",
    "allowed_modes",
    "apply_overrides",
    "check_service",
    "clear_overrides",
    "set_override",
    "to_declared",
    "to_internal",
]

#: ``set_mode`` calls not yet folded onto a configuration, keyed by service and
#: holding the *internal* mode string.
_OVERRIDES: dict[str, str] = {}


def check_service(service: str) -> str:
    """Validate *service* against the mockable set, returning it unchanged.

    A name outside the set raises rather than resolving to a default: silently
    answering ``"mock"`` for a typo would report a service as mocked that the
    run never had.
    """
    if service not in MOCKABLE_SERVICES:
        raise ValueError(
            f"Unknown service {service!r}. Valid services: {', '.join(sorted(MOCKABLE_SERVICES))}"
        )
    return service


def allowed_modes(service: str) -> tuple[str, ...]:
    """The modes *service* can actually be set to."""
    check_service(service)
    if service in NO_LOCAL_BACKEND:
        return ("mock", "disabled")
    return DECLARED_MODES


def to_declared(internal_mode: str) -> str:
    """Collapse a configured mode string onto the three declared modes.

    The configuration's vocabulary is open, while the declared modes are closed.
    Anything that is neither the mock nor disabled is a real backend, so it reports
    as ``"local"``.
    """
    if internal_mode in ("mock", "disabled"):
        return internal_mode
    return "local"


def to_internal(service: str, declared_mode: str) -> str:
    """Translate a declared mode into the string the configuration stores."""
    check_service(service)
    permitted = allowed_modes(service)
    if declared_mode not in permitted:
        if declared_mode in DECLARED_MODES:
            raise ValueError(
                f"Service {service!r} has no local backend to point at; "
                f"valid modes: {', '.join(permitted)}"
            )
        raise ValueError(f"Unknown mode {declared_mode!r}. Valid modes: {', '.join(permitted)}")
    if service == "llm" and declared_mode == "local":
        return "byo-key"
    return declared_mode


def set_override(service: str, internal_mode: str) -> None:
    """Record a mode for every configuration built from here on."""
    _OVERRIDES[service] = internal_mode


def apply_overrides(config: Any) -> None:
    """Fold every recorded :func:`set_override` onto *config*."""
    mocks = getattr(config, "mocks", None)
    if mocks is None or not _OVERRIDES:
        return
    mocks.modes.update(_OVERRIDES)


def clear_overrides() -> None:
    """Drop every recorded override (tests, and process-level reset)."""
    _OVERRIDES.clear()
