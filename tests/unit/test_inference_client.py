"""Unit tests for perceivo.intelligence.inference.predict (internal routing wrapper)."""

from __future__ import annotations

import pytest

from perceivo.intelligence import inference


async def test_predict_outside_context_raises(cleared_context):
    # Called outside component execution -> get_ext() raises.
    with pytest.raises(RuntimeError):
        await inference.predict("00000000-0000-0000-0042-000000000001", {"user_prompt": "hi"})


async def test_predict_delegates_to_ext(stub_ext):
    captured: dict = {}

    async def predict_inference(*, intelligence_id, features, timeout_seconds):
        captured.update(
            intelligence_id=intelligence_id,
            features=features,
            timeout_seconds=timeout_seconds,
        )
        return {
            "prediction": [0.9],
            "model_id": "m-1",
            "metadata": {},
            "correlation_id": "c-1",
        }

    stub_ext.predict_inference = predict_inference
    out = await inference.predict("iid-1", {"user_prompt": "hello"}, timeout_seconds=30)
    assert out.prediction == [0.9]
    assert out.model_id == "m-1"
    assert out.correlation_id == "c-1"
    assert captured == {
        "intelligence_id": "iid-1",
        "features": {"user_prompt": "hello"},
        "timeout_seconds": 30,
    }


async def test_predict_uses_default_timeout(stub_ext):
    captured: dict = {}

    async def predict_inference(*, intelligence_id, features, timeout_seconds):
        captured["timeout_seconds"] = timeout_seconds
        return {
            "prediction": None,
            "model_id": "m",
            "metadata": {},
            "correlation_id": "c",
        }

    stub_ext.predict_inference = predict_inference
    await inference.predict("iid", {})
    assert captured["timeout_seconds"] == 60
