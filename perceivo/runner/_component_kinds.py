"""Local dispatch for intelligence and capability components.

``runner.run_component`` runs one component against a supplied input. For the
eight orchestration primitives that is one method call
(:mod:`._component_execution`). An intelligence or capability component is
driven by a platform loop instead: the feature service walks batches, the
training service walks iterations, the evaluation service scores runs and then
plans traffic. Running one locally drives that loop once, in process, over the
input the caller supplied.

What each drive calls, and the platform loop it follows:

========== ================================================================
FEATURE    ``get_schema`` -> ``prepare_data(IngestInput)`` -> ``process_batch``
           for every batch in the plan (the feature service ingest loop).
TRAINING   ``initialize`` -> ``run_iteration`` until the plan is exhausted or
           an iteration reports ``is_complete`` -> ``evaluate``
           (the training service).
INFERENCE  load the model bundle named by ``model_path`` (a directory holding
           ``_manifest.json``) onto the instance, then
           ``predict(InferenceInput)`` -> ``postprocess``
           (the inference service).
EVALUATION ``get_evaluation_config`` -> ``offline_evaluation`` ->
           ``should_promote``. The evaluation service's offline route calls
           only ``offline_evaluation``, once per family; the platform requires
           the other two but does not call them.
SCORING    ``score_execution(context)`` with the context the platform builds
           for a test run.
EVALUATOR  ``score_run(context)`` then ``online_evaluation(...)``, the order
           the evaluation service's online route calls them in.
========== ================================================================

The request data supplies the loop's inputs under the keys each drive
documents; anything absent takes the empty value the platform would pass.
"""

from __future__ import annotations

import inspect
import logging
import math
from types import SimpleNamespace
from typing import Any

from ..validator._component_tables import INTELLIGENCE_TYPES
from .types import ExecutionResult

CAPABILITY_TYPES = frozenset({"SCORING", "EVALUATOR"})
COMPONENT_KIND_TYPES = INTELLIGENCE_TYPES | CAPABILITY_TYPES

logger = logging.getLogger("perceivo.runner")

#: Mirrors the scoring context the platform builds for a test run.
_SCORING_CONTEXT_DEFAULTS: dict[str, Any] = {
    "execution_events": [],
    "input_payload": {},
    "output_payload": {},
    "review_response": None,
    "review_survey_spec": None,
    "orchestration_id": "",
    "orchestration_name": "",
    "capability_id": "",
    "workload_id": "",
    "run_id": "",
    "run_metrics": {},
    "capability_benchmarks": {},
}


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _dump(value: Any) -> Any:
    """A pydantic model as a plain dict; anything else unchanged."""
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value


async def _preloaded_ingest(ingest: dict[str, Any]) -> dict[str, Any]:
    """Hand the component its source files as ``{file_name: bytes}``, as the platform does.

    The platform downloads every file directly inside the ingest folder before
    ``prepare_data`` and passes them as ``source_config["file_contents"]``. Here the
    folder is ``source_path`` under the local org files root. Contents given inline
    are used instead, with text encoded as UTF-8, since the platform hands bytes.
    """
    source_config = dict(ingest.get("source_config") or {})
    contents = source_config.get("file_contents")
    if isinstance(contents, dict) and contents:
        source_config["file_contents"] = {
            str(name): data.encode("utf-8") if isinstance(data, str) else data
            for name, data in contents.items()
        }
    elif ingest.get("source_path"):
        source_config["file_contents"] = await _folder_files(str(ingest["source_path"]))
    ingest["source_config"] = source_config
    return ingest


async def _folder_files(folder: str) -> dict[str, bytes]:
    """The files directly inside *folder* of the org files; empty when there are none.

    The platform's pre-load is best-effort, so a missing folder or unavailable
    org files leaves the component to report the empty source itself.
    """
    from .._internal.context import _ExecutionContext

    org_files: Any = getattr(_ExecutionContext.get_ext(), "org_files", None)
    # Best-effort, as the platform's pre-load is: a failure leaves the source empty.
    try:
        entries = await org_files.list(parent_id=folder, entry_type="file")
        return {entry["name"]: await org_files.read(entry["id"]) for entry in entries}
    except Exception:
        logger.warning("Could not pre-load the ingest folder %r", folder, exc_info=True)
        return {}


async def _run_feature(instance: Any, request_data: dict[str, Any]) -> dict[str, Any]:
    from ..types import IngestInput

    schema = await _maybe_await(instance.get_schema())
    ingest = await _preloaded_ingest(dict(request_data.get("ingest") or {}))
    plan = await instance.prepare_data(IngestInput(**ingest))
    batch_size = max(int(plan.batch_size or 1), 1)
    batches = math.ceil(plan.total_items / batch_size) if plan.total_items else 0
    records_processed = 0
    errors: list[str] = []
    for index in range(batches):
        batch = await instance.process_batch(index)
        records_processed += batch.records_processed
        errors.extend(batch.errors or [])
    return {
        "schema": _dump(schema),
        "batches": batches,
        "records_processed": records_processed,
        "errors": errors,
    }


async def _run_training(instance: Any, request_data: dict[str, Any]) -> dict[str, Any]:
    plan = await instance.initialize(
        request_data.get("train_data"),
        request_data.get("val_data"),
        request_data.get("hyperparameters", {}),
    )
    iterations: list[dict[str, Any]] = []
    for index in range(plan.total_iterations):
        result = await instance.run_iteration(index)
        iterations.append(dict(result.metrics))
        if getattr(result, "is_complete", False):
            break
    evaluation = await instance.evaluate()
    return {"iterations": iterations, "metrics": dict(evaluation.metrics)}


async def _run_inference(instance: Any, request_data: dict[str, Any]) -> dict[str, Any]:
    from ..types import InferenceInput
    from ._model_bundle import (
        MODEL_PATH_KEY,
        ModelBundleError,
        load_model_bundle,
        missing_bundle_message,
    )

    model_path = request_data.get(MODEL_PATH_KEY)
    if model_path:
        load_model_bundle(instance, str(model_path))
    inp = InferenceInput(
        features=request_data.get("features", {}),
        metadata=request_data.get("metadata") or {},
    )
    try:
        prediction = await instance.predict(inp)
    except AttributeError as exc:
        # An attribute the component reads off itself and nothing set is a bundle
        # artifact on the platform; name it rather than surface the bare lookup.
        if exc.obj is instance and exc.name and not hasattr(instance, exc.name):
            message = missing_bundle_message(type(instance).__name__, exc.name, model_path)
            raise ModelBundleError(message) from exc
        raise
    return _dump(await instance.postprocess(prediction, inp))  # type: ignore[no-any-return]


async def _run_evaluation(instance: Any, request_data: dict[str, Any]) -> dict[str, Any]:
    config = await _maybe_await(instance.get_evaluation_config())
    output = await instance.offline_evaluation(
        training_results=request_data.get("training_results", []),
        champion_metrics=request_data.get("champion_metrics", {}),
        config=request_data.get("config", {}),
        family_config=request_data.get("family_config", {}),
    )
    decision = await instance.should_promote(output)
    return {
        "config": _dump(config),
        "offline_evaluation": _dump(output),
        "promotion": _dump(decision),
    }


async def _run_scoring(instance: Any, request_data: dict[str, Any]) -> dict[str, Any]:
    context = SimpleNamespace(**{**_SCORING_CONTEXT_DEFAULTS, **request_data})
    return dict(await _maybe_await(instance.score_execution(context)) or {})


async def _run_evaluator(instance: Any, request_data: dict[str, Any]) -> dict[str, Any]:
    run = request_data.get("run_snapshot", {})
    context = SimpleNamespace(
        run_snapshot=SimpleNamespace(
            run_id=run.get("run_id", ""),
            orchestration_id=run.get("orchestration_id", ""),
            workload_id=run.get("workload_id", ""),
            input_data=run.get("input_data", {}),
            output_data=run.get("output_data", {}),
            elapsed_duration_ms=run.get("elapsed_duration_ms"),
            node_timing=run.get("node_timing", []),
            provenance=SimpleNamespace(review_response=run.get("review_response")),
        ),
        capability_id=request_data.get("capability_id", ""),
        organization_id=request_data.get("organization_id", ""),
    )
    score = dict(await instance.score_run(context) or {})
    evaluation = await instance.online_evaluation(
        current_traffic_plan=request_data.get("current_traffic_plan", {"allocations": []}),
        aggregated_scores=request_data.get("aggregated_scores", {}),
        model_stats={},
        config={},
        family_config={},
        context_type="orchestration",
        resource_id=context.capability_id,
        resource_stats=None,
        orchestration_stats={},
        review_stats=None,
        billing_usage=request_data.get("billing_usage"),
    )
    return {"score_run": score, "online_evaluation": _dump(evaluation)}


_DRIVES = {
    "FEATURE": _run_feature,
    "TRAINING": _run_training,
    "INFERENCE": _run_inference,
    "EVALUATION": _run_evaluation,
    "SCORING": _run_scoring,
    "EVALUATOR": _run_evaluator,
}


async def execute_component_kind(
    instance: Any, component_type: str, request_data: dict[str, Any]
) -> ExecutionResult:
    """Drive one intelligence or capability component through its loop once."""
    response = await _DRIVES[component_type](instance, request_data)
    return ExecutionResult(success=True, response_data=response)
