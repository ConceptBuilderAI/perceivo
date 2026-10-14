"""The per-type tables the component validator reads."""

from __future__ import annotations

from typing import Optional

# Only ``@abstractmethod`` members belong in COMPONENT_REQUIRED_METHODS: requiring a
# concrete, overridable helper would fail a component for declining an option.

COMPONENT_REQUIRED_METHODS: dict[str, list[str]] = {
    "AGENT": ["run"],
    "TOOL": ["run"],
    "FSM": ["run"],
    "POLICY": ["run"],
    "START": ["start"],
    "END": ["end"],
    "REVIEW": ["run"],
    "ADAPTER": ["run"],
    "FEATURE": ["get_schema", "prepare_data", "process_batch", "get_samples"],
    "TRAINING": ["initialize", "run_iteration", "evaluate"],
    "INFERENCE": ["predict"],
    "EVALUATION": [
        "get_evaluation_config",
        "offline_evaluation",
        "online_evaluation",
        "should_promote",
    ],
    "SCORING": ["score_execution"],
    "EVALUATOR": ["score_run", "online_evaluation"],
}

COMPONENT_BASES: dict[str, str] = {
    "AGENT": "Agent",
    "TOOL": "Tool",
    "FSM": "FSM",
    "POLICY": "Policy",
    "START": "Start",
    "END": "End",
    "ADAPTER": "Adapter",
    # A REVIEW component may carry no script; when it has one, its entry class
    # inherits ``perceivo.primitives.Review``.
    "REVIEW": "Review",
    "FEATURE": "FeatureComponent",
    "TRAINING": "TrainingComponent",
    "INFERENCE": "InferenceComponent",
    "EVALUATION": "EvaluationComponent",
    "SCORING": "ScoringComponent",
    "EVALUATOR": "EvaluatorComponent",
}

#: The package each base in :data:`COMPONENT_BASES` is imported from, so a
#: base-class finding names the import the author actually needs.
_BASE_HOMES: dict[str, str] = {
    "FEATURE": "perceivo.intelligence",
    "TRAINING": "perceivo.intelligence",
    "INFERENCE": "perceivo.intelligence",
    "EVALUATION": "perceivo.intelligence",
    "SCORING": "perceivo.capability",
    "EVALUATOR": "perceivo.capability",
}

DECORATOR_NAMES = {
    "agent",
    "tool",
    "start",
    "end",
    "fsm",
    "policy",
    "adapter",
    "review",
}


ALL_COMPONENT_TYPES = {
    "AGENT",
    "TOOL",
    "FSM",
    "POLICY",
    "START",
    "END",
    "REVIEW",
    "ADAPTER",
    "SCORING",
    "EVALUATOR",
    "FEATURE",
    "TRAINING",
    "INFERENCE",
    "EVALUATION",
}

INTELLIGENCE_TYPES = frozenset({"FEATURE", "TRAINING", "INFERENCE", "EVALUATION"})

# These types are loaded by resolvers that take ENTRY_CLASS as the class itself as
# well as its name; every other type is resolved by name only.
CLASS_ENTRY_TYPES = INTELLIGENCE_TYPES | {"EVALUATOR"}


def unknown_component_type_message(component_type: Optional[str]) -> str:
    """The platform's refusal of a component type it does not create, in its words."""
    return (
        f"Unknown component_type '{component_type}'. "
        f"Must be one of: {', '.join(sorted(ALL_COMPONENT_TYPES))}."
    )
