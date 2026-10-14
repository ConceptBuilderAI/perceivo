"""Build AI orchestration components with the Perceivo SDK.

Create, validate, and run orchestrations locally or in Perceivo Cloud.

    pip install perceivo

Everything is reached as ``perceivo.<plane>.<name>``; the orchestration
primitives are under ``perceivo.primitives``::

    import perceivo
    from perceivo.primitives import AgentInput, AgentOutput, Artifact

    @perceivo.agent
    async def my_agent(input: AgentInput) -> AgentOutput:
        perceivo.log.info("processing", rows=100)
        return AgentOutput(artifact=Artifact(type="text", data="done"))

The root holds planes, eight decorators and ``__version__``, nothing else.
The decorators ``start``, ``end``, ``agent``, ``tool``, ``fsm``, ``policy``,
``adapter`` and ``review`` are re-exported from :mod:`perceivo.primitives` as
the same objects, so ``@perceivo.agent`` and
``from perceivo.primitives import agent`` are one decorator. The base classes
and the input/output types are not re-exported: they live in
:mod:`perceivo.primitives` only.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Static declarations only: normal imports must keep the local authoring
    # plane unloaded until __getattr__ receives an allowlisted name.
    from . import bootstrap as bootstrap
    from . import component as component
    from . import config as config
    from . import local as local
    from . import orchestration as orchestration
    from . import runner as runner
    from . import templates as templates
    from . import validator as validator


def _resolve_version() -> str:
    """Return the installed SDK version.

    Reads the installed distribution metadata, then the repository ``VERSION``
    file for a source checkout, and returns ``"0.0.0+unknown"`` if neither is
    readable.
    """
    try:
        from importlib.metadata import version as _dist_version

        return _dist_version("perceivo")
    except Exception:
        pass
    try:
        from pathlib import Path

        return (
            (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
        )
    except Exception:
        return "0.0.0+unknown"


__version__ = _resolve_version()

from . import (
    alerts,
    capabilities,
    capability,
    cluster,
    cost,
    files,
    flow,
    intelligence,
    log,
    managed_service,
    metrics,
    params,
    primitives,
    reviews,
    secrets,
    stream,
    types,
)
from .primitives import adapter, agent, end, fsm, policy, review, start, tool

#: The primitive decorators re-exported at the root.
_ROOT_DECORATORS = ("adapter", "agent", "end", "fsm", "policy", "review", "start", "tool")

# Planes, the eight decorators in ``_ROOT_DECORATORS``, and ``__version__``.
__all__ = [
    "__version__",
    "adapter",
    "agent",
    "alerts",
    "bootstrap",
    "capabilities",
    "capability",
    "cluster",
    "component",
    "config",
    "cost",
    "end",
    "files",
    "flow",
    "fsm",
    "intelligence",
    "local",
    "log",
    "managed_service",
    "metrics",
    "orchestration",
    "params",
    "policy",
    "primitives",
    "review",
    "reviews",
    "runner",
    "secrets",
    "start",
    "stream",
    "templates",
    "tool",
    "types",
    "validator",
]


# Local authoring modules load on first access. The runtime sandbox, not this
# allowlist, is the enforcement boundary.
_LOCAL_PLANE = frozenset(
    {
        "bootstrap",
        "component",
        "config",
        "local",
        "orchestration",
        "runner",
        "templates",
        "validator",
    }
)


def __getattr__(name: str) -> Any:
    """Bind a local-plane module on first access."""
    if name in _LOCAL_PLANE:
        import importlib

        module = importlib.import_module("." + name, __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Keep the lazy planes visible to ``dir()``, tab completion and ``help()``."""
    return sorted(set(globals()) | _LOCAL_PLANE)
