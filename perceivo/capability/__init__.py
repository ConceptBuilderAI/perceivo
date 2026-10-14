"""Define capability components, which judge a capability's runs.

A capability evaluator runs in the evaluation service beside a capability,
not inside an orchestration graph. There are two kinds::

    perceivo.capability.ScoringComponent     scores one execution
    perceivo.capability.EvaluatorComponent   scores runs and plans traffic

The component types are ``SCORING`` and ``EVALUATOR``; the platform requires
``score_execution`` on the first and ``score_run`` plus ``online_evaluation`` on
the second.

This plane holds the base classes. Reading a capability's runs and execution
graphs is :mod:`perceivo.capabilities`.
"""

from ._components import EvaluatorComponent, ScoringComponent

__all__ = ["EvaluatorComponent", "ScoringComponent"]
