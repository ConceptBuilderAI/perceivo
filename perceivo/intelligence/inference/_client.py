"""Run predictions against deployed intelligence services.

Prediction requests are routed through the egress broker to the inference
service.
"""

from __future__ import annotations

from typing import Any

from ..._internal.context import _ExecutionContext
from ...types.errors import NotFoundError
from ...types.records import Prediction, _from_wire
from .._types import PredictionRecord


async def predict(
    intelligence_id: str,
    features: dict[str, Any],
    timeout_seconds: int = 60,
) -> Prediction:
    """Run a prediction against a deployed intelligence service.

    The request is routed through the egress broker, and the platform enforces
    the INTELLIGENCE_PREDICT privilege.

    Args:
        intelligence_id: UUID of the intelligence to invoke.
        features: Input features dict passed to the model.
        timeout_seconds: Request timeout (default 60s).

    Returns:
        A :class:`~perceivo.types.records.Prediction` with ``prediction`` (the
        model's output, in whatever shape the model has), ``model_id``,
        ``metadata`` and ``correlation_id``.

    Raises:
        RuntimeError: If called outside component execution context.
        ConfigurationError: Under the local runtime with the ``inference``
            service disabled. When mocked (the default), the local runtime
            returns a canned prediction.

    Example::

        import perceivo

        class MyTool(perceivo.primitives.Tool):
            async def run(self, input: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
                result = await perceivo.intelligence.inference.predict(
                    intelligence_id="00000000-0000-0000-0042-000000000001",
                    features={"user_prompt": input.request_data["question"]},
                )
                return perceivo.primitives.ToolOutput(
                    response_data={"answer": result.prediction}
                )
    """
    ext = _ExecutionContext.get_ext()
    return _from_wire(
        Prediction,
        await ext.predict_inference(
            intelligence_id=intelligence_id,
            features=features,
            timeout_seconds=timeout_seconds,
        ),
    )


async def predictions(
    intelligence_id: str | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    correlation_id: str | None = None,
    request_id: str | None = None,
    entity_id: str | None = None,
    model_id: str | None = None,
    limit: int = 100,
) -> list[PredictionRecord]:
    """Read predictions an intelligence has already served.

    Predictions are logs: :func:`predict` writes one on every call and this
    reads them back. The window is bounded: an absent ``start`` looks back seven
    days. ``limit`` caps what is returned and there is no total, so a full page
    means there may be more.

    An omitted ``intelligence_id`` reads the log of the intelligence the calling
    component is running under. Naming an id explicitly still works and is
    subject to the same tenant check.

    Args:
        intelligence_id: UUID of the intelligence whose predictions to read.
            Defaults to the intelligence the calling component is executing
            under. Outside an intelligence (in an orchestration or a capability
            component) there is nothing to default to, and the call raises.
        start: ISO-8601 or ``YYYY-MM-DD`` lower bound. Defaults to seven days
            ago.
        end: Upper bound. Open-ended when absent.
        correlation_id: Match the id the caller supplied when predicting.
        request_id: Match the platform request id. Wins over
            ``correlation_id`` when both are given, since the two select the
            same underlying field.
        entity_id: Match the subject of the prediction.
        model_id: Match the model that served it.
        limit: Maximum records to return (1-1000).

    Returns:
        :class:`~perceivo.intelligence._types.PredictionRecord` objects,
        newest first. Empty when nothing matched in the window.

    Raises:
        RuntimeError: If called outside component execution context, or if
            ``intelligence_id`` was omitted and the calling component is not
            executing under an intelligence.
        AttributeError: Outside an evaluation service or feature service
            extension. Reading prediction history requires the
            ``workload:read`` capability, which the inference token does not
            carry.
        NotFoundError: If the intelligence does not exist, has been deleted,
            or belongs to another organization. The three are one answer, so a
            caller cannot learn whether a UUID names an intelligence it has no
            right to see. Never returned as an empty list.

    Example::

        # The intelligence this component is running under.
        recent = await perceivo.intelligence.inference.predictions(limit=50)
        errors = [p for p in recent if p.is_error]

        # Some other intelligence, named explicitly.
        theirs = await perceivo.intelligence.inference.predictions(
            other_id, entity_id="user-42", limit=50
        )
    """
    ext = _ExecutionContext.get_ext()
    raw = await ext.list_predictions(
        intelligence_id=intelligence_id,
        start=start,
        end=end,
        correlation_id=correlation_id,
        request_id=request_id,
        entity_id=entity_id,
        model_id=model_id,
        limit=limit,
    )
    # None and [] are different answers: [] means the query ran and matched
    # nothing, None means there is no such intelligence to query.
    if raw is None:
        subject = intelligence_id or "the executing intelligence"
        raise NotFoundError(f"Intelligence not found: {subject}")
    return [_from_wire(PredictionRecord, p) for p in raw]
