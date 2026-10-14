"""The local inference mock behind ``intelligence.inference.predict``."""

from __future__ import annotations

from typing import Any, cast

import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext
from perceivo.config import Config, MocksConfig
from perceivo.local.context import LocalExtensionManager
from perceivo.local.mocks.inference import MockInference
from perceivo.local.mocks.seed import DEFAULT_SEED


@pytest.fixture
def runtime():
    managers: list[LocalExtensionManager] = []

    def _make(**mocks) -> LocalExtensionManager:
        ext = LocalExtensionManager(Config(mocks=MocksConfig(**mocks)))
        _ExecutionContext.clear_all()
        _ExecutionContext.set_ext(ext)
        managers.append(ext)
        return ext

    yield _make
    _ExecutionContext.clear_all()


@pytest.mark.asyncio
async def test_the_default_prediction_is_the_shipped_seed(runtime) -> None:
    ext = runtime()
    result = await perceivo.intelligence.inference.predict("intel-1", {"user_prompt": "hi"})
    assert result.prediction == DEFAULT_SEED["inference"]["default_prediction"]
    assert result.model_id == "mock-model"
    assert result.metadata == {"mock": True}
    calls = ext._mocks.trace.calls
    assert [(c.service, c.operation) for c in calls] == [("inference", "predict")]


@pytest.mark.asyncio
async def test_a_seed_chooses_the_prediction_per_intelligence(runtime) -> None:
    runtime(
        seed={
            "inference": {
                "prediction": {"label": "default"},
                "predictions": {"intel-2": {"label": "two"}},
                "model_id": "m-7",
            }
        }
    )
    first = await perceivo.intelligence.inference.predict("intel-1", {})
    second = await perceivo.intelligence.inference.predict("intel-2", {})
    again = await perceivo.intelligence.inference.predict("intel-2", {})
    assert first.prediction == {"label": "default"}
    assert second.prediction == again.prediction == {"label": "two"}
    assert second.model_id == "m-7"


@pytest.mark.asyncio
async def test_a_caller_mutating_its_prediction_does_not_change_the_next() -> None:
    mock = MockInference({"prediction": {"k": [1]}})
    body = await mock.predict(intelligence_id="i")
    body["prediction"]["k"].append(2)
    assert (await mock.predict(intelligence_id="i"))["prediction"] == {"k": [1]}


@pytest.mark.asyncio
async def test_an_empty_seed_answers_with_none_and_the_default_model() -> None:
    body = await MockInference({}).predict(intelligence_id="i")
    assert body["prediction"] is None
    assert body["model_id"] == "mock-model"


@pytest.mark.asyncio
async def test_without_a_mock_registry_the_call_refuses() -> None:
    ext = LocalExtensionManager(Config())
    # Past the declared type on purpose: the refusal path is what a None registry reaches.
    cast(Any, ext)._mocks = None
    with pytest.raises(NotImplementedError, match="no local backend"):
        await ext.predict_inference(intelligence_id="i", features={})
