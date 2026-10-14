"""Every primitive and component kind runs under the local runner.

Components created in code must run locally: every new primitive (``Review``) and every
``*Component`` must run under the local runner, with a test. This is that test:
each kind is written as a real script, loaded the way ``perceivo run
component`` loads it, and driven through ``runner.run_component``.

``Review`` keeps today's local behaviour -- the runner auto-approves -- until
the review lifecycle in plan section 2.3 replaces it.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from perceivo.runner import run_component


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


# --- Review -------------------------------------------------------------------


REVIEW_CLASS = """
import perceivo
from perceivo.primitives import Review, ReviewInput, ReviewOutput

class Approve(Review):
    async def run(self, input: ReviewInput) -> ReviewOutput:
        return ReviewOutput(artifact=input.artifact, response_data={"seen": True})

ENTRY_CLASS = "Approve"
"""

REVIEW_DECORATOR = """
import perceivo
from perceivo.primitives import ReviewInput, ReviewOutput

@perceivo.review
async def approve(input: ReviewInput) -> ReviewOutput:
    return ReviewOutput(artifact=input.artifact, response_data={"seen": True})
"""


@pytest.mark.parametrize("body", [REVIEW_CLASS, REVIEW_DECORATOR], ids=["class", "decorator"])
async def test_a_review_component_runs_locally_and_is_auto_approved(tmp_path, body):
    result = await run_component(_script(tmp_path, "review", body), component_type="REVIEW")
    assert result.success, result.error
    # The component ran, and today's local decision payload rides over it.
    assert result.response_data["seen"] is True
    assert result.response_data["approved"] is True
    assert result.response_data["rating"] == 5


# --- intelligence components --------------------------------------------------


FEATURE = """
from perceivo.intelligence import FeatureComponent
from perceivo.types import (
    BatchResult, FeatureSchema, IngestPlan, PropertyDefinition, PropertyType,
)

class Features(FeatureComponent):
    def get_schema(self):
        return FeatureSchema(
            properties={
                "user_id": PropertyDefinition(name="user_id", dtype=PropertyType.STRING),
                "ts": PropertyDefinition(name="ts", dtype=PropertyType.TIMESTAMP),
            },
            entity_key=["user_id"],
            timestamp_field="ts",
        )

    async def prepare_data(self, input):
        self.rows = [{"user_id": str(i)} for i in range(5)]
        return IngestPlan(total_items=len(self.rows), batch_size=2)

    async def process_batch(self, batch_index):
        chunk = self.rows[batch_index * 2 : batch_index * 2 + 2]
        return BatchResult(records={"users": chunk}, records_processed=len(chunk))

    async def get_samples(self, request):
        yield None

ENTRY_CLASS = "Features"
"""

TRAINING = """
from perceivo.intelligence import TrainingComponent
from perceivo.types import EvaluationResult, IterationResult, TrainingPlan

class Train(TrainingComponent):
    async def initialize(self, train_data, val_data, hyperparameters):
        self.loss = 1.0
        return TrainingPlan(total_iterations=hyperparameters["epochs"])

    async def run_iteration(self, iteration):
        self.loss /= 2
        return IterationResult(metrics={"loss": self.loss}, is_complete=iteration == 1)

    async def evaluate(self):
        return EvaluationResult(metrics={"final_loss": self.loss})

ENTRY_CLASS = "Train"
"""

INFERENCE = """
from perceivo.intelligence import InferenceComponent

class Infer(InferenceComponent):
    async def predict(self, input):
        return {"doubled": input.features["x"] * 2}

ENTRY_CLASS = "Infer"
"""

EVALUATION = """
from perceivo.intelligence import EvaluationComponent
from perceivo.types import (
    EvaluationConfig, OfflineEvaluationOutput, OnlineEvaluationOutput, PromotionDecision,
)

class Judge(EvaluationComponent):
    def get_evaluation_config(self):
        return EvaluationConfig(metrics=[])

    async def offline_evaluation(self, training_results, champion_metrics, config, family_config):
        return OfflineEvaluationOutput(evaluation_summary=f"runs={len(training_results)}")

    async def online_evaluation(self, current_traffic_plan, model_stats, config, family_config):
        return OnlineEvaluationOutput()

    async def should_promote(self, evaluation):
        return PromotionDecision(should_promote=True, reason="best so far")

ENTRY_CLASS = "Judge"
"""


async def test_a_feature_component_runs_every_batch(tmp_path):
    result = await run_component(_script(tmp_path, "feat", FEATURE), component_type="FEATURE")
    assert result.success, result.error
    assert result.response_data["batches"] == 3
    assert result.response_data["records_processed"] == 5


async def test_a_training_component_runs_until_complete(tmp_path):
    result = await run_component(
        _script(tmp_path, "train", TRAINING),
        component_type="TRAINING",
        input_data={"hyperparameters": {"epochs": 5}},
    )
    assert result.success, result.error
    assert result.response_data["iterations"] == [{"loss": 0.5}, {"loss": 0.25}]
    assert result.response_data["metrics"] == {"final_loss": 0.25}


async def test_an_inference_component_predicts_and_postprocesses(tmp_path):
    result = await run_component(
        _script(tmp_path, "infer", INFERENCE),
        component_type="INFERENCE",
        input_data={"features": {"x": 21}},
    )
    assert result.success, result.error
    assert result.response_data["result"] == {"doubled": 42}


async def test_an_evaluation_component_evaluates_and_decides(tmp_path):
    result = await run_component(
        _script(tmp_path, "judge", EVALUATION),
        component_type="EVALUATION",
        input_data={"training_results": [{"metrics": {}}]},
    )
    assert result.success, result.error
    assert result.response_data["offline_evaluation"]["evaluation_summary"] == "runs=1"
    assert result.response_data["promotion"]["should_promote"] is True


# --- capability components ----------------------------------------------------


SCORING = """
from perceivo.capability import ScoringComponent

class LengthScore(ScoringComponent):
    async def score_execution(self, context):
        text = str(context.output_payload.get("result", ""))
        return {"score": len(text) / 10, "breakdown": {"run": context.run_id}}

ENTRY_CLASS = "LengthScore"
"""

EVALUATOR = """
from perceivo.capability import EvaluatorComponent

class KeepPlan(EvaluatorComponent):
    async def score_run(self, context):
        return {"score": 1.0 if context.run_snapshot.output_data else 0.0}

    async def online_evaluation(
        self, current_traffic_plan, aggregated_scores, model_stats, config, family_config,
        context_type, resource_id, resource_stats, orchestration_stats, review_stats,
        billing_usage,
    ):
        return {"traffic_plan": current_traffic_plan, "context": context_type}

ENTRY_CLASS = "KeepPlan"
"""


async def test_a_scoring_component_scores_an_execution(tmp_path):
    result = await run_component(
        _script(tmp_path, "score", SCORING),
        component_type="SCORING",
        input_data={"output_payload": {"result": "0123456789"}, "run_id": "r1"},
    )
    assert result.success, result.error
    assert result.response_data == {"score": 1.0, "breakdown": {"run": "r1"}}


async def test_an_evaluator_component_scores_a_run_then_plans_traffic(tmp_path):
    result = await run_component(
        _script(tmp_path, "evaluator", EVALUATOR),
        component_type="EVALUATOR",
        input_data={"run_snapshot": {"output_data": {"ok": True}}, "capability_id": "cap"},
    )
    assert result.success, result.error
    assert result.response_data["score_run"] == {"score": 1.0}
    assert result.response_data["online_evaluation"] == {
        "traffic_plan": {"allocations": []},
        "context": "orchestration",
    }


async def test_a_component_that_raises_fails_the_run(tmp_path):
    body = SCORING.replace('return {"score"', 'raise ValueError("boom")\n        return {"score"')
    result = await run_component(_script(tmp_path, "boom", body), component_type="SCORING")
    assert result.success is False
    assert "boom" in result.error


# --- reviews: the results read the REVIEW node's answers come back through ----


async def test_review_results_are_read_as_review_result_records(stub_ext):
    import perceivo

    row = {
        "review_request_id": "r1",
        "workload_id": "w1",
        "survey_spec": [{"id": "q1", "type": "RATING_STARS"}],
        "status": "COMPLETED",
        "created_at": "2026-09-28T00:00:00Z",
        "component_id": "c1",
        "response_data": {"q1": 4},
        "unknown_future_key": "dropped",
    }
    seen: dict = {}

    async def get_review_results(**kwargs):
        seen.update(kwargs)
        return [row]

    stub_ext.get_review_results = get_review_results
    page = await perceivo.reviews.query(
        subject=perceivo.reviews.ReviewSubject.COMPONENT, subject_id="c1", limit=10
    )
    [result] = page.data
    assert isinstance(result, perceivo.reviews.ReviewResult)
    assert result.response_data == {"q1": 4}
    assert seen == {
        "component_id": "c1",
        "orchestration_id": None,
        "capability_id": None,
        "status": "COMPLETED",
        "limit": 10,
    }
