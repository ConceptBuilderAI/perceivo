"""An evaluation written to the SDK runs when called the way the platform calls it.

The platform calls every evaluation method with keyword arguments only. The
fixture holds one such call per method. Each is made against the SDK's base
method, against a subclass written to it, and through the local runner, so a
base signature that the platform's call cannot bind fails here first.
"""

from __future__ import annotations

import inspect
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from perceivo.capability import EvaluatorComponent
from perceivo.intelligence import EvaluationComponent
from perceivo.runner import run_component
from perceivo.types import (
    EvaluationConfig,
    OfflineEvaluationInput,
    OfflineEvaluationOutput,
    OnlineEvaluationInput,
    OnlineEvaluationOutput,
    PromotionDecision,
)

CALLS: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "platform_evaluation_calls.json").read_text(
        encoding="utf-8"
    )
)
OFFLINE = "intelligence_offline_evaluation"
ONLINE = "intelligence_online_evaluation"
CAPABILITY = "capability_online_evaluation"

_METHODS = {
    OFFLINE: (EvaluationComponent, "offline_evaluation"),
    ONLINE: (EvaluationComponent, "online_evaluation"),
    CAPABILITY: (EvaluatorComponent, "online_evaluation"),
}


class _Evaluation(EvaluationComponent):
    def get_evaluation_config(self) -> EvaluationConfig:
        return EvaluationConfig(metrics=[])

    async def offline_evaluation(
        self,
        training_results: list[dict[str, Any]],
        champion_metrics: dict[str, Any],
        config: dict[str, Any],
        family_config: dict[str, Any],
    ) -> OfflineEvaluationOutput:
        return OfflineEvaluationOutput(evaluation_summary=f"runs={len(training_results)}")

    async def online_evaluation(
        self,
        current_traffic_plan: dict[str, Any],
        model_stats: dict[str, Any],
        config: dict[str, Any],
        family_config: dict[str, Any],
    ) -> OnlineEvaluationOutput:
        return OnlineEvaluationOutput(message=f"models={len(model_stats)}")

    async def should_promote(self, evaluation: OfflineEvaluationOutput) -> PromotionDecision:
        return PromotionDecision(should_promote=False)


class _Evaluator(EvaluatorComponent):
    async def score_run(self, context: Any) -> dict[str, Any]:
        return {"score": 1.0}

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
        return {"traffic_plan": current_traffic_plan, "resource_id": resource_id}


@pytest.mark.parametrize("call", list(_METHODS))
def test_the_base_method_declares_exactly_the_platform_keywords(call):
    base, name = _METHODS[call]
    parameters = list(inspect.signature(getattr(base, name)).parameters)
    assert parameters == ["self", *CALLS[call]]


@pytest.mark.parametrize("call", list(_METHODS))
async def test_the_platform_call_binds_to_the_base_method(call):
    base, name = _METHODS[call]
    instance = _Evaluation() if base is EvaluationComponent else _Evaluator()
    # The base body raises NotImplementedError only once the call has bound.
    with pytest.raises(NotImplementedError):
        await getattr(base, name)(instance, **CALLS[call])


async def test_a_subclass_runs_the_offline_call():
    output = await _Evaluation().offline_evaluation(**CALLS[OFFLINE])
    assert output.evaluation_summary == "runs=1"


async def test_a_subclass_runs_the_online_call():
    output = await _Evaluation().online_evaluation(**CALLS[ONLINE])
    assert output.message == "models=1"


async def test_a_subclass_runs_the_capability_call():
    result = await _Evaluator().online_evaluation(**CALLS[CAPABILITY])
    assert result["resource_id"] == "cap-1"


@pytest.mark.parametrize(
    ("call", "model"),
    [(OFFLINE, OfflineEvaluationInput), (ONLINE, OnlineEvaluationInput)],
)
def test_the_input_type_models_the_call(call, model):
    assert model(**CALLS[call]).model_dump(include=set(CALLS[call])) == CALLS[call]


def test_the_online_input_type_models_the_capability_call():
    call = CALLS[CAPABILITY]
    assert set(OnlineEvaluationInput.model_fields) == set(call)
    dumped = OnlineEvaluationInput(**call).model_dump(mode="json")
    assert dumped == call


def test_the_offline_input_type_has_no_field_the_platform_does_not_pass():
    assert set(OfflineEvaluationInput.model_fields) == set(CALLS[OFFLINE])


ONLINE_RESULT: dict[str, Any] = {
    "traffic_plan": {
        "families": [
            {
                "family_id": "default",
                "traffic_percent": 100.0,
                "models": [
                    {"model_id": "m-champion", "role": "champion", "traffic_percent": 90.0},
                    {"model_id": "m-candidate", "role": "canary", "traffic_percent": 10.0},
                ],
            }
        ]
    },
    "message": "Shift 10% to m-candidate",
    "evaluation_report": {"m-candidate": {"error_rate": 0.01}},
    "outcome_events": [{"event_type": "canary_started", "model_id": "m-candidate"}],
}


def _platform_reads(result: Any) -> dict[str, Any]:
    """Read one family's online result the way the evaluation service does."""
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    traffic_plan = result.get("traffic_plan")
    families = traffic_plan.get("families", []) if isinstance(traffic_plan, dict) else []
    return {
        "traffic_plan": {"families": families},
        "message": result.get("message"),
        "evaluation_report": result.get("evaluation_report"),
        "outcome_events": result.get("outcome_events") or [],
    }


def test_the_online_output_type_declares_exactly_what_the_platform_reads():
    assert set(OnlineEvaluationOutput.model_fields) == set(ONLINE_RESULT)


def test_the_platform_reads_every_field_of_a_returned_online_output():
    assert _platform_reads(OnlineEvaluationOutput(**ONLINE_RESULT)) == ONLINE_RESULT


def test_an_empty_online_output_reads_as_no_traffic_change():
    assert _platform_reads(OnlineEvaluationOutput()) == {
        "traffic_plan": {"families": []},
        "message": None,
        "evaluation_report": None,
        "outcome_events": [],
    }


_SPY_EVALUATION = """
from perceivo.intelligence import EvaluationComponent
from perceivo.types import EvaluationConfig, OfflineEvaluationOutput, PromotionDecision

class Spy(EvaluationComponent):
    def get_evaluation_config(self):
        return EvaluationConfig(metrics=[])

    async def offline_evaluation(self, **kwargs):
        return OfflineEvaluationOutput(metrics_comparison=kwargs)

    async def online_evaluation(self, **kwargs):
        raise AssertionError("not called offline")

    async def should_promote(self, evaluation):
        return PromotionDecision(should_promote=False)

ENTRY_CLASS = "Spy"
"""

_SPY_EVALUATOR = """
from perceivo.capability import EvaluatorComponent

class Spy(EvaluatorComponent):
    async def score_run(self, context):
        return {"score": 1.0}

    async def online_evaluation(self, **kwargs):
        return {"received": kwargs}

ENTRY_CLASS = "Spy"
"""


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "spy.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


async def test_the_local_runner_makes_the_platform_offline_call(tmp_path):
    result = await run_component(
        _script(tmp_path, _SPY_EVALUATION),
        component_type="EVALUATION",
        input_data=CALLS[OFFLINE],
    )
    assert result.success, result.error
    assert result.response_data["offline_evaluation"]["metrics_comparison"] == CALLS[OFFLINE]


async def test_the_local_runner_makes_the_platform_capability_call(tmp_path):
    call = CALLS[CAPABILITY]
    result = await run_component(
        _script(tmp_path, _SPY_EVALUATOR),
        component_type="EVALUATOR",
        input_data={
            "current_traffic_plan": call["current_traffic_plan"],
            "aggregated_scores": call["aggregated_scores"],
            "capability_id": call["resource_id"],
            "billing_usage": call["billing_usage"],
        },
    )
    assert result.success, result.error
    assert result.response_data["online_evaluation"]["received"] == call
