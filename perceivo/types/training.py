"""Define the result types a training component returns.

The typed pair for :class:`perceivo.intelligence.TrainingComponent`. A training
extension implements three methods and each returns one of these::

    initialize(train_data, val_data, hyperparameters) -> TrainingPlan
    run_iteration(iteration)                          -> IterationResult
    evaluate()                                        -> EvaluationResult

``IterationResult`` and ``EvaluationResult`` both reject non-finite metrics.
The platform's early-stopping and promotion logic compares these numbers, so a
NaN loss is rejected at the boundary rather than propagated into a checkpoint.

Example:
    ```python
    from perceivo.intelligence import TrainingComponent
    from perceivo.types import EvaluationResult, IterationResult, TrainingPlan

    class MyTraining(TrainingComponent):
        async def initialize(self, train_data, val_data, hyperparameters):
            self.epochs = hyperparameters.get("epochs", 10)
            return TrainingPlan(total_iterations=self.epochs)

        async def run_iteration(self, iteration: int) -> IterationResult:
            loss = self._train_one_epoch()
            return IterationResult(metrics={"loss": loss})

        async def evaluate(self) -> EvaluationResult:
            return EvaluationResult(metrics={"accuracy": self._accuracy()})
    ```
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator


def _require_finite_metrics(v: dict[str, float]) -> dict[str, float]:
    """Reject non-numeric, NaN and infinite metric values."""
    for key, val in v.items():
        if not isinstance(val, (int, float)):
            raise ValueError(f"Metric '{key}' must be numeric, got {type(val).__name__}")
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"Metric '{key}' must be finite, got {val}")
    return v


class TrainingPlan(BaseModel):
    """Returned by ``TrainingComponent.initialize()``.

    Tells the platform how many iterations the run will take, so it can drive
    the loop, report progress, and honour pause/stop/cancel. ``iteration_name``
    is what one iteration is called in logs and in the UI: "epoch" for most
    models, "round" for federated training, "generation" for evolutionary
    search.
    """

    total_iterations: int = Field(
        ..., gt=0, description="Total number of iterations (epochs) to run"
    )
    iteration_name: str = Field(
        default="epoch", description="What to call each iteration (for logging)"
    )


class IterationResult(BaseModel):
    """Returned by ``TrainingComponent.run_iteration()`` after each training step.

    ``is_complete`` lets an extension stop early, on convergence or its own
    early-stopping rule, without running out the plan's iteration count.
    """

    metrics: dict[str, float] = Field(
        ..., description="Metrics from this iteration (e.g., train_loss, val_acc)"
    )
    is_complete: bool = Field(default=False, description="Set True to signal early convergence")

    @field_validator("metrics")
    @classmethod
    def metrics_are_finite(cls, v: dict[str, float]) -> dict[str, float]:
        """Ensure all metrics are finite numbers."""
        return _require_finite_metrics(v)


class EvaluationResult(BaseModel):
    """
    Returned by ``TrainingComponent.evaluate()`` with the final model metrics.

    These are what the evaluation service compares against the champion when
    deciding promotion.
    """

    metrics: dict[str, float] = Field(..., description="Final evaluation metrics")

    @field_validator("metrics")
    @classmethod
    def metrics_are_finite(cls, v: dict[str, float]) -> dict[str, float]:
        """Ensure all metrics are finite numbers."""
        return _require_finite_metrics(v)
