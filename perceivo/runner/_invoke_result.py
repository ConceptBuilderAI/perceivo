"""Invoke-result shaping and parallel failure-strategy resolution.

Both helpers match the platform engine, so the local runner returns the same
invoke contract and applies the same parallel failure semantics.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("perceivo.runner")

# Parallel failure strategies:
#   - "wait_all"        : wait for all branches, then raise if any failed (default).
#   - "fail_fast"       : raise on the first branch failure.
#   - "ignore_failures" : return per-branch errors instead of raising.
DEFAULT_FAILURE_STRATEGY = "wait_all"
VALID_FAILURE_STRATEGIES = ("wait_all", "fail_fast", "ignore_failures")


def format_invoke_result(result: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Shape an invoke result to match the platform's return contract.

    The dict handed back to a calling component is ``response_data`` spread at the
    top level, plus ``artifact`` (an :class:`~perceivo.primitives.Artifact` object,
    not a serialized dict), ``artifacts``, a nested ``response_data``, and ``log``.
    ``result["artifact"]["data"]`` is supported through the Artifact mapping API.
    The internal ``conversation_log`` key is surfaced as ``log``.
    """
    if not result:
        return {}
    response_data = result.get("response_data") or {}
    response: dict[str, Any] = dict(response_data)
    response["artifact"] = result.get("artifact")
    response["artifacts"] = result.get("artifacts")
    response["response_data"] = response_data
    response["log"] = result.get("conversation_log")
    return response


def resolve_failure_strategy(failure_strategy: Optional[str]) -> str:
    """Resolve the effective failure strategy (mirrors the engine's resolver).

    An explicit ``failure_strategy`` wins; absent, the fail-closed
    ``"wait_all"`` applies. An unrecognized value falls back to that default.
    """
    if failure_strategy:
        strategy = failure_strategy.strip().lower()
        if strategy in VALID_FAILURE_STRATEGIES:
            return strategy
        logger.warning(
            "[runner] Unknown failure_strategy=%r; falling back to %r",
            failure_strategy,
            DEFAULT_FAILURE_STRATEGY,
        )
        return DEFAULT_FAILURE_STRATEGY
    return DEFAULT_FAILURE_STRATEGY
