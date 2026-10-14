"""
Define the evaluation types.

The typed pair for :class:`perceivo.intelligence.EvaluationComponent`. An
evaluation extension implements four methods::

    get_evaluation_config()                 -> EvaluationConfig
    offline_evaluation(training_results, champion_metrics, config, family_config)
                                            -> OfflineEvaluationOutput
    online_evaluation(current_traffic_plan, model_stats, config, family_config)
                                            -> OnlineEvaluationOutput
    should_promote(OfflineEvaluationOutput) -> PromotionDecision

The platform calls both evaluations with keyword arguments.
:class:`OfflineEvaluationInput` and :class:`OnlineEvaluationInput` model those
arguments, so ``OfflineEvaluationInput(**kwargs)`` reads one call's values.

Offline evaluation runs after training and decides what happens to the new
models. Online evaluation runs periodically, once per model family, against live
prediction statistics and proposes that family's traffic plan.

Example:
    ```python
    from perceivo.types import EvaluationConfig, MetricDefinition, MetricType

    config = EvaluationConfig(
        metrics=[
            MetricDefinition(name="ndcg@10", metric_type=MetricType.HIGHER_IS_BETTER,
                             threshold=0.3),
            MetricDefinition(name="latency_p99_ms", metric_type=MetricType.LOWER_IS_BETTER,
                             threshold=100.0, weight=0.5),
        ],
        min_improvement_percent=5.0,
    )
    ```
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MetricType(str, Enum):
    """Which direction counts as better for a metric."""

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    CLOSEST_TO = "closest_to"


class MetricDefinition(BaseModel):
    """
    Definition of an evaluation metric.

    ``threshold`` is a minimum for HIGHER_IS_BETTER and CLOSEST_TO, and a
    maximum for LOWER_IS_BETTER. ``target_value`` is only meaningful for
    CLOSEST_TO.
    """

    name: str = Field(..., description="Metric name (e.g. 'accuracy', 'ndcg@10')")
    metric_type: MetricType = Field(
        default=MetricType.HIGHER_IS_BETTER, description="Optimization direction"
    )
    threshold: float | None = Field(
        default=None, description="Threshold for acceptable performance"
    )
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="Relative importance weight")
    target_value: float | None = Field(
        default=None, description="Target value for CLOSEST_TO metrics"
    )
    description: str | None = Field(default=None, description="Human-readable metric description")


class EvaluationConfig(BaseModel):
    """
    Configuration for model evaluation, returned by
    ``EvaluationComponent.get_evaluation_config()``.
    """

    metrics: list[MetricDefinition] = Field(
        default_factory=list, description="List of metrics to evaluate"
    )
    min_improvement_percent: float = Field(
        default=5.0, ge=0.0, description="Minimum % improvement over champion for promotion"
    )
    require_all_metrics: bool = Field(
        default=False, description="If True, all metrics must meet their thresholds"
    )
    evaluation_window_hours: int = Field(
        default=24, ge=1, description="Time window for online evaluation metrics"
    )


class EvaluationContext(str, Enum):
    """Whether an evaluation is of an intelligence (ML models) or a capability."""

    INTELLIGENCE = "intelligence"
    ORCHESTRATION = "orchestration"


class EvaluationActionType(str, Enum):
    """Actions the evaluation service can take as a result of an evaluation."""

    PROMOTE_MODEL = "promote_model"
    DEMOTE_MODEL = "demote_model"
    UPDATE_TRAFFIC = "update_traffic"
    FREEZE_PROMOTIONS = "freeze_promotions"
    UNFREEZE_PROMOTIONS = "unfreeze_promotions"
    CREATE_ALERT = "create_alert"
    TRIGGER_RETRAINING = "trigger_retraining"
    NO_ACTION = "no_action"


class EvaluationAction(BaseModel):
    """
    One action for the platform to execute.

    An extension returns these from ``offline_evaluation()``; the platform is
    what actually promotes, demotes, re-weights traffic or raises the alert.
    ``reason`` is required because every action is surfaced to an operator.
    """

    action_type: EvaluationActionType = Field(..., description="Type of action to take")
    model_id: str | None = Field(
        default=None, description="ID of the model (for PROMOTE/DEMOTE actions)"
    )
    from_status: str | None = Field(
        default=None, description="Current model status (candidate, canary, champion, retired)"
    )
    to_status: str | None = Field(
        default=None, description="Target model status (canary, champion, retired)"
    )
    traffic_plan: dict[str, Any] | None = Field(
        default=None, description="Traffic plan for UPDATE_TRAFFIC actions"
    )
    alert_severity: str | None = Field(
        default=None,
        description="Severity for CREATE_ALERT actions (info, warning, error, critical)",
    )
    alert_message: str | None = Field(default=None, description="Message for CREATE_ALERT actions")
    reason: str = Field(..., description="Explanation for the action")


class OfflineEvaluationInput(BaseModel):
    """
    The keyword arguments of ``EvaluationComponent.offline_evaluation()``.

    The platform calls it once per model family with exactly these four.
    """

    training_results: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Training results, each with a model_id and metrics; only this family's when the "
            "intelligence has several"
        ),
    )
    champion_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="The current champion's training metrics; empty when there is none",
    )
    config: dict[str, Any] = Field(default_factory=dict, description="Intelligence configuration")
    family_config: dict[str, Any] = Field(
        default_factory=dict, description="This model family's configuration"
    )


class OfflineEvaluationOutput(BaseModel):
    """
    Returned by ``EvaluationComponent.offline_evaluation()``, and the input to
    ``should_promote()``.
    """

    model_actions: list[EvaluationAction] = Field(
        default_factory=list, description="Actions to take on the evaluated models"
    )
    evaluation_summary: str = Field(default="", description="Human-readable evaluation summary")
    metrics_comparison: dict[str, Any] | None = Field(
        default=None, description="Detailed metrics comparison data"
    )
    timestamp: datetime | None = Field(default=None, description="Evaluation timestamp")


class OnlineEvaluationInput(BaseModel):
    """
    The keyword arguments of an online evaluation.

    In the intelligence context the platform calls
    ``EvaluationComponent.online_evaluation()`` once per model family with
    ``current_traffic_plan``, ``model_stats``, ``config`` and ``family_config``;
    the other fields keep their defaults.

    In the orchestration context it calls
    ``EvaluatorComponent.online_evaluation()`` with every field.
    ``model_stats``, ``config`` and ``family_config`` are empty dicts there, and
    of the statistics only ``billing_usage`` carries data.
    """

    current_traffic_plan: dict[str, Any] = Field(
        default_factory=dict,
        description="The traffic plan as it stands; {'allocations': [...]} for a capability",
    )
    model_stats: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Live statistics keyed by model_id: prediction_count, error_count, error_rate, "
            "latency_p50_ms, latency_p95_ms, latency_p99_ms, custom_metrics and "
            "sample_predictions"
        ),
    )
    config: dict[str, Any] = Field(default_factory=dict, description="Intelligence configuration")
    family_config: dict[str, Any] = Field(
        default_factory=dict, description="This model family's configuration"
    )
    aggregated_scores: dict[str, Any] = Field(
        default_factory=dict,
        description="The capability's aggregated run scores (orchestration context only)",
    )
    context_type: EvaluationContext = Field(
        default=EvaluationContext.INTELLIGENCE,
        description="Whether this is an intelligence or orchestration evaluation",
    )
    resource_id: str | None = Field(
        default=None, description="The capability_id being evaluated (orchestration context only)"
    )
    resource_stats: dict[str, Any] | None = Field(
        default=None, description="Not supplied by the platform; stays None"
    )
    orchestration_stats: dict[str, Any] | None = Field(
        default=None, description="Not supplied; an empty dict in the orchestration context"
    )
    review_stats: dict[str, Any] | None = Field(
        default=None, description="Not supplied by the platform; stays None"
    )
    billing_usage: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The organization's orchestration usage over the last 24 hours "
            "(orchestration context only; None when the read fails)"
        ),
    )


class OnlineEvaluationOutput(BaseModel):
    """Returned by ``EvaluationComponent.online_evaluation()``.

    These four fields are all the platform reads from the result; a dict with
    the same keys is read the same way.
    """

    traffic_plan: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The family's proposed traffic, as "
            '``{"families": [{"family_id", '
            '"traffic_percent", "models": [{"model_id", "role", "traffic_percent"}]}]}``; '
            "None proposes no traffic change"
        ),
    )
    message: str | None = Field(default=None, description="A summary shown to the operator")
    evaluation_report: dict[str, Any] | None = Field(
        default=None, description="A structured report recorded with the evaluation"
    )
    outcome_events: list[dict[str, Any]] = Field(
        default_factory=list, description="Events the platform records for this evaluation"
    )


class PromotionDecision(BaseModel):
    """Returned by ``EvaluationComponent.should_promote()``."""

    should_promote: bool = Field(..., description="Whether to promote the model")
    target_status: str | None = Field(default=None, description="Target status (canary, champion)")
    model_id: str | None = Field(default=None, description="ID of the model to promote")
    reason: str = Field(default="", description="Explanation for the decision")
    metrics_comparison: dict[str, Any] | None = Field(
        default=None, description="Metrics comparison supporting the decision"
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Confidence in the decision"
    )
