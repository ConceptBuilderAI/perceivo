"""Provide the outside-a-run AI runtime behind :mod:`perceivo.managed_service.ai`.

``ai.py`` keeps the public surface and the dispatch. This module holds the
runtime that dispatch uses when there is no orchestration executor, and
nothing here is reachable inside a run.

``_MOCKED_NOTICE`` is emitted on the ``perceivo.managed_service.ai`` logger,
not this module's, because it describes the call the user made.

``perceivo.config`` and ``perceivo.local`` are imported inside the functions
that need them, keeping the local plane out of the eager import graph.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any, cast

from .._internal.context import _ExecutionContext
from ..types.errors import ConfigurationError

__all__ = ["_standalone_runtime"]

logger = logging.getLogger("perceivo.managed_service.ai")

_MOCKED_NOTICE = (
    "MOCKED: perceivo.managed_service.ai served this call from the local mock, "
    "not from the platform AI Foundry. No model was invoked and no tokens were "
    "billed, so a result derived from it is not evidence about a real model. "
    "To call the real service, set ai_foundry.enabled + ai_foundry.api_key in "
    "perceivo.yaml (or PERCEIVO_AI_FOUNDRY_ENABLED / PERCEIVO_LLM_API_KEY)."
)


class _StandaloneExt:
    """Expose only the three AI methods of the extension manager surface.

    Outside a run this plane serves AI calls and nothing more; reaching for any
    other platform capability through this object raises ``AttributeError``.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def invoke_ai_foundry(self, **kwargs: Any) -> dict[str, Any]:
        return cast(dict[str, Any], await self._provider.invoke(**kwargs))

    async def embed_ai_foundry(self, **kwargs: Any) -> dict[str, Any]:
        return cast(dict[str, Any], await self._provider.embed(**kwargs))

    async def list_ai_foundry_models(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], await self._provider.list_models())


@contextlib.contextmanager
def _standalone_runtime() -> Iterator[None]:
    """Push a config-resolved AI provider for the duration of one call.

    Inside and outside a run differ only in which extension manager is on the
    stack. The provider is popped in ``finally``, so one that raises leaves no
    context behind for the next call in the same task.
    """
    from ..config import load

    _ExecutionContext.set_ext(_StandaloneExt(_provider(load())))
    try:
        yield
    finally:
        _ExecutionContext.clear()


def _provider(config: Any) -> Any:
    """Resolve the mock or the real provider from *config*.

    Never both, and never a silent downgrade from one to the other.

    Args:
        config: The local configuration.

    Returns:
        The mock or real provider.
    """
    if config.service_mode("llm") == "mock":
        # Warn on every call so a mocked answer is never mistaken for a model's.
        logger.warning("%s", _MOCKED_NOTICE)
        from ..local.mocks.llm import MockLLM
        from ..local.mocks.seed import load_seed

        seed = load_seed(getattr(config, "mocks", None)).get("llm")
        return MockLLM(seed if isinstance(seed, dict) else {})

    _require_credential(config.ai_foundry)
    from ..local.adapters.ai_foundry import LocalAIFoundryAdapter

    # No ``secret_resolver``: outside a run there is no secrets service, and the
    # adapter's environment sweep must not be reached. ``api_key`` is set here.
    return LocalAIFoundryAdapter(config.ai_foundry)


def _require_credential(ai_foundry: Any) -> None:
    """Refuse a real call with no configured credential. Never fall back.

    The error names the absent config keys and no values: not the key that is
    set, not a prefix of it, not its length.

    Args:
        ai_foundry: The ``ai_foundry`` config section.

    Raises:
        ConfigurationError: If a required key is missing.
    """
    missing: list[str] = []
    if not getattr(ai_foundry, "enabled", False):
        missing.append("ai_foundry.enabled")
    # Strip first: a whitespace-only key is truthy but unusable.
    if not str(getattr(ai_foundry, "api_key", None) or "").strip():
        missing.append("ai_foundry.api_key")
    if not missing:
        return

    alias_note = ""
    if getattr(ai_foundry, "api_key_secret", None) and not getattr(ai_foundry, "api_key", None):
        alias_note = (
            " `ai_foundry.api_key_secret` is set, but a bound-secret alias is "
            "resolved by the runtime's secrets service, and outside a run there "
            "is no runtime to resolve it against. Supply `ai_foundry.api_key` "
            "for standalone use; the alias keeps working inside a run."
        )

    raise ConfigurationError(
        f"perceivo.managed_service.ai was asked for the real platform AI "
        f"service and {' and '.join(missing)} "
        f"{'is' if len(missing) == 1 else 'are'} not set."
        f"{alias_note}\n"
        f"Set them in perceivo.yaml:\n"
        f"  ai_foundry:\n"
        f"    enabled: true\n"
        f"    provider: openai        # or openai_compatible / azure_openai / anthropic\n"
        f"    api_key: <your key>\n"
        f"or export PERCEIVO_AI_FOUNDRY_ENABLED=true and PERCEIVO_LLM_API_KEY.\n"
        f"Not falling back to the mock: a run that asked for the real service "
        f"and quietly got a mock is green and proves nothing."
    )
