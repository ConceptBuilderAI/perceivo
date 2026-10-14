"""Mock inference service for ``intelligence.inference.predict``, offline.

Hosted runs answer ``perceivo.intelligence.inference.predict`` through the
extension manager's ``predict_inference``, returning ``prediction``,
``model_id``, ``metadata`` and ``correlation_id`` (see
:class:`perceivo.types.records.Prediction`). Locally the call is served here in
the same shape. There is no local inference backend: the service is ``mock`` or
``disabled``.

The answer is a canned, deterministic value, and the mock trace records every
call. Seed ``mocks.seed.inference`` to choose it:

``prediction`` (any)
    The model output returned for every intelligence, used as given.
``predictions`` (mapping of intelligence id to any)
    Per-intelligence overrides of ``prediction``.
``default_prediction`` (any)
    Returned when neither of the above answers. The shipped value has the shape
    of the platform's execution-graph classifier (a ``result`` object with
    ``predicted_orchestration_id`` and ``confidence``). It is separate from
    ``prediction`` because seeds deep-merge over the shipped defaults.
``model_id`` (str)
    The model id reported on every prediction.
"""

from __future__ import annotations

import copy
from typing import Any

_DEFAULT_MODEL_ID = "mock-model"


class MockInference:
    """Deterministic, seedable stand-in for the inference service."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}

    def _prediction_for(self, intelligence_id: str) -> Any:
        overrides = self._seed.get("predictions")
        if isinstance(overrides, dict) and intelligence_id in overrides:
            return copy.deepcopy(overrides[intelligence_id])
        if "prediction" in self._seed:
            return copy.deepcopy(self._seed["prediction"])
        return copy.deepcopy(self._seed.get("default_prediction"))

    async def predict(
        self,
        *,
        intelligence_id: str = "",
        features: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Return an inference service response body for *intelligence_id*."""
        return {
            "prediction": self._prediction_for(str(intelligence_id)),
            "model_id": str(self._seed.get("model_id") or _DEFAULT_MODEL_ID),
            "metadata": {"mock": True},
            "correlation_id": "mock-correlation-id",
        }
