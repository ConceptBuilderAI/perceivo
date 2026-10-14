"""
Inference types for perceivo SDK.

The typed pair for :class:`perceivo.intelligence.InferenceComponent`: what a
prediction receives, and what it returns.

Example:
    ```python
    from perceivo.intelligence import InferenceComponent
    from perceivo.types import InferenceInput, InferenceOutput

    class MyInference(InferenceComponent):
        async def predict(self, input: InferenceInput) -> InferenceOutput:
            score = self.model(input.features["vector"])
            return InferenceOutput(result={"score": score})
    ```
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InferenceInput(BaseModel):
    """
    Input to ``InferenceComponent.predict()``.

    ``features`` carries the request payload. ``metadata`` carries
    request-scoped context the platform attaches; it is not part of the
    model's input.
    """

    features: dict[str, Any] = Field(..., description="Input features for the prediction")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Request-scoped context attached by the platform"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class InferenceOutput(BaseModel):
    """
    Returned by ``InferenceComponent.postprocess()``.

    ``result`` is whatever the extension produced: a scalar, a mapping or a
    ranked list. ``model_version`` identifies which model served the request
    and defaults to ``"1.0"``; the base ``InferenceComponent.postprocess()``
    fills it from the extension's ``_model_version``, but an override that
    constructs ``InferenceOutput(result=...)`` itself gets ``"1.0"``.
    """

    result: Any = Field(..., description="The prediction result")
    model_version: str = Field("1.0", description="Version of the model that served the request")
    latency_ms: float | None = Field(
        default=None, ge=0, description="Server-side prediction latency in milliseconds"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)
