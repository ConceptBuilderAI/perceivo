"""Session bootstrap for using the SDK outside a component.

In an experiment kernel, a Jupyter notebook or a standalone script, there is
no orchestration runtime to push an execution context, so this plane pushes
one.

- ``start`` opens a session and makes it the active execution context.
- ``stop`` closes it.
- ``session()`` returns the active session, or ``None``.

``Session`` is not exported here; it is the type ``start`` returns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types.errors import ContextError
from . import _session as _impl

if TYPE_CHECKING:
    from pathlib import Path

    from ._session import Session

__all__ = ["session", "start", "stop"]


async def start(
    *,
    egress_url: str | None = None,
    execution_token: str | None = None,
    organization_id: str | None = None,
    config_path: str | Path | None = None,
    local: bool = False,
) -> Session:
    """Open a session and make it the active execution context.

    Args:
        egress_url: Egress broker URL. Auto-detected from ``EGRESS_BROKER_URL``.
        execution_token: Execution token for broker auth. Auto-detected from
            ``EXECUTION_TOKEN``.
        organization_id: Organization UUID. Auto-detected from
            ``ORGANIZATION_ID``.
        config_path: Path to a ``perceivo.yaml`` for local development.
        local: Use local development mode, loading the file
            ``PERCEIVO_CONFIG`` names, else ``./perceivo.yaml``.

    Returns:
        The active session. On the platform path this is a ``Session``; under
        ``local=True`` or ``config_path=`` it is a ``LocalExtensionManager``.

    Raises:
        RuntimeError: A required value was neither passed nor present in the
            environment.
    """
    return _impl.connect(
        egress_url=egress_url,
        execution_token=execution_token,
        organization_id=organization_id,
        config_path=None if config_path is None else str(config_path),
        local=local,
    )


async def stop(session: Session | None = None) -> None:
    """Close the active session and clear the execution context.

    Calling ``start()`` twice replaces the active session, so a caller can hold a
    stale handle; passing one raises rather than stopping a different session.

    Args:
        session: The session to stop. ``None`` (the default) stops whichever
            session is active, and is a no-op when there is none.

    Raises:
        ~perceivo.types.errors.ContextError: *session* was given and is not the
            active session.
    """
    active = _impl._active_session
    if session is not None and session is not active:
        raise ContextError(
            "perceivo.bootstrap.stop(session=…) can only stop the active "
            "session. The session passed is not active"
            + (" (no session is active)." if active is None else ".")
            + " Call stop() with no argument to stop whichever session is "
            "active."
        )
    _impl.disconnect()


def session() -> Session | None:
    """Return the active session, or ``None``.

    The global is read through the module rather than imported by name, because
    an imported name would bind ``None`` at import time.
    """
    return _impl._active_session
