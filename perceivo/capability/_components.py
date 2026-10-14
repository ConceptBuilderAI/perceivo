"""Base classes for capability components (SCORING and EVALUATOR).

The platform calls each method with these arguments:

* ``score_run(context)``: ``context`` carries ``run_snapshot`` (``run_id``,
  ``orchestration_id``, ``workload_id``, ``input_data``, ``output_data``,
  ``elapsed_duration_ms``, ``node_timing``, ``provenance.review_response``),
  ``capability_id`` and ``organization_id``. The returned dict's ``score``,
  ``breakdown``, ``metadata``, ``tags`` and ``scoring_duration_ms`` are
  recorded.
* ``online_evaluation(...)``: eleven keyword arguments,
  ``current_traffic_plan``, ``aggregated_scores``, ``model_stats``,
  ``config``, ``family_config``, ``context_type``, ``resource_id`` and the
  statistics ``resource_stats`` (``None``), ``orchestration_stats``
  (``{}``), ``review_stats`` (``None``) and ``billing_usage``; it returns a
  dict whose ``allocations`` the platform applies. ``model_stats``,
  ``config`` and ``family_config`` are empty dicts.
* ``score_execution(context)``: ``context`` carries ``execution_events``,
  ``input_payload``, ``output_payload``, ``review_response``,
  ``review_survey_spec``, ``orchestration_id``, ``orchestration_name``,
  ``capability_id``, ``workload_id``, ``run_id``, ``run_metrics`` and
  ``capability_benchmarks``, and returns a dict.

The contexts are attribute objects built by the platform, so they are typed
``Any``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

__all__ = ["EvaluatorComponent", "ScoringComponent"]


class ScoringComponent(ABC):
    """
    Base class for SCORING components.

    A scoring component scores one execution of a capability. Return a dict
    with at least ``score`` (a float); ``breakdown``, ``metadata`` and ``tags``
    are recorded with it when present.

    Example:
        ```python
        from perceivo.capability import ScoringComponent

        class LengthScore(ScoringComponent):
            async def score_execution(self, context):
                text = str(context.output_payload.get("result", ""))
                return {"score": min(len(text) / 100, 1.0), "breakdown": {"length": len(text)}}

        ENTRY_CLASS = "LengthScore"
        ```
    """

    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook called once before the first call."""
        pass

    @abstractmethod
    async def score_execution(self, context: Any) -> dict[str, Any]:
        """
        Score one execution.

        Args:
            context: The scoring context (see the module docstring for its
                attributes).

        Returns:
            A dict with ``score`` and optionally ``breakdown``, ``metadata`` and
            ``tags``.
        """
        raise NotImplementedError("Subclass must implement score_execution()")


class EvaluatorComponent(ABC):
    """
    Base class for EVALUATOR components.

    An evaluator scores a capability's completed runs one at a time
    (:meth:`score_run`) and then decides how traffic should be allocated from
    the aggregated scores (:meth:`online_evaluation`).

    Example:
        ```python
        from perceivo.capability import EvaluatorComponent

        class KeepPlan(EvaluatorComponent):
            async def score_run(self, context):
                ok = bool(context.run_snapshot.output_data)
                return {"score": 1.0 if ok else 0.0}

            async def online_evaluation(
                self, current_traffic_plan, aggregated_scores, model_stats, config,
                family_config, context_type, resource_id, resource_stats,
                orchestration_stats, review_stats, billing_usage,
            ):
                return {"allocations": current_traffic_plan["allocations"]}

        ENTRY_CLASS = "KeepPlan"
        ```
    """

    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook called once before the first call."""
        pass

    @abstractmethod
    async def score_run(self, context: Any) -> dict[str, Any]:
        """
        Score one completed run.

        Args:
            context: Carries ``run_snapshot``, ``capability_id`` and
                ``organization_id`` (see the module docstring).

        Returns:
            A dict with ``score`` and optionally ``breakdown``, ``metadata``,
            ``tags`` and ``scoring_duration_ms``. A ``None`` score is recorded
            only when a ``breakdown`` explains it.
        """
        raise NotImplementedError("Subclass must implement score_run()")

    @abstractmethod
    async def online_evaluation(
        self,
        current_traffic_plan: dict[str, Any],
        aggregated_scores: dict[str, Any],
        model_stats: dict[str, Any],
        config: dict[str, Any],
        family_config: dict[str, Any],
        context_type: str,
        resource_id: str,
        resource_stats: dict[str, Any] | None,
        orchestration_stats: dict[str, Any],
        review_stats: dict[str, Any] | None,
        billing_usage: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Decide the capability's traffic plan from the aggregated scores.

        The platform passes all eleven as keyword arguments.
        ``OnlineEvaluationInput(**kwargs)`` models them.

        Args:
            current_traffic_plan: ``{"allocations": [...]}`` as it stands.
            aggregated_scores: The capability's aggregated run scores.
            model_stats: An empty dict.
            config: An empty dict.
            family_config: An empty dict.
            context_type: ``"orchestration"``.
            resource_id: The capability's id.
            resource_stats: ``None``; not supplied.
            orchestration_stats: An empty dict; not supplied.
            review_stats: ``None``; not supplied.
            billing_usage: The organization's orchestration usage over the
                last 24 hours, or ``None`` when that read fails.

        Returns:
            A dict the platform reads for ``allocations``, the plan to apply as a
            list of ``{"orchestration_id", "role", "traffic_percent"}`` (empty or
            absent leaves traffic unchanged), and for ``message``,
            ``evaluation_report`` and ``notes``.
        """
        raise NotImplementedError("Subclass must implement online_evaluation()")
