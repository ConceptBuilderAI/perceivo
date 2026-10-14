"""``perceivo.flow`` and ``perceivo.cost`` wrappers.

``perceivo.cost.record`` is a coroutine function
(``perceivo.cost._recorder.record``); there is no sync dispatch to cover, and
every test here is ``await``.
"""

from __future__ import annotations

import pytest

from perceivo import cost, flow

# ---------------------------------------------------------------------------
# flow.invoke / flow.invoke_parallel
# ---------------------------------------------------------------------------


async def test_flow_invoke_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await flow.invoke("port_x")


async def test_flow_invoke_requires_orchestration_context(stub_ext):
    # No invoke_port attr on stub -> RuntimeError with "only available in orchestrations"
    with pytest.raises(RuntimeError, match="only available in orchestrations"):
        await flow.invoke("port_x")


async def test_flow_invoke_delegates_to_ext(stub_ext):
    captured = {}

    async def invoke_port(*, port_name, request_data, artifact, artifacts):
        captured["port_name"] = port_name
        captured["request_data"] = request_data
        return {"response_data": {"ok": True}}

    stub_ext.invoke_port = invoke_port
    result = await flow.invoke("my_port", request_data={"x": 1})
    assert captured["port_name"] == "my_port"
    assert captured["request_data"] == {"x": 1}
    assert result == {"response_data": {"ok": True}}


async def test_flow_invoke_defaults_empty_request_data(stub_ext):
    captured = {}

    async def invoke_port(*, port_name, request_data, artifact, artifacts):
        captured.update({"rd": request_data, "art": artifact, "arts": artifacts})
        return {}

    stub_ext.invoke_port = invoke_port
    await flow.invoke("p")
    assert captured["rd"] == {}
    assert captured["art"] is None
    assert captured["arts"] == {}


async def test_flow_invoke_parallel_delegates(stub_ext):
    captured = {}

    async def _noop(*a, **kw):
        return {}

    async def invoke_port_parallel(**kwargs):
        captured.update(kwargs)
        return {"a": {"response_data": {}}, "b": {"response_data": {}}}

    stub_ext.invoke_port = _noop  # required by orchestration-context guard
    stub_ext.invoke_port_parallel = invoke_port_parallel
    result = await flow.invoke_parallel(
        ["a", "b"], timeout_seconds=10, failure_strategy="fail_fast"
    )
    assert set(result.keys()) == {"a", "b"}
    assert captured["port_names"] == ["a", "b"]
    assert captured["timeout_seconds"] == 10
    assert captured["failure_strategy"] == "fail_fast"
    assert "fail_fast" not in captured


# --- failure_strategy is threaded through to the engine ---


def _capturing_parallel_stub(stub_ext):
    """Wire stub_ext with a parallel callback that records the forwarded kwargs."""
    captured: dict = {}

    async def _noop(*a, **kw):
        return {}

    async def invoke_port_parallel(**kwargs):
        captured.update(kwargs)
        return {"a": {"response_data": {}}}

    stub_ext.invoke_port = _noop  # required by orchestration-context guard
    stub_ext.invoke_port_parallel = invoke_port_parallel
    return captured


async def test_flow_invoke_parallel_forwards_failure_strategy(stub_ext):
    captured = _capturing_parallel_stub(stub_ext)
    await flow.invoke_parallel(["a"], failure_strategy="ignore_failures")
    # The opt-out reaches the ext/engine call verbatim.
    assert captured["failure_strategy"] == "ignore_failures"


async def test_flow_invoke_parallel_default_is_fail_closed(stub_ext):
    captured = _capturing_parallel_stub(stub_ext)
    await flow.invoke_parallel(["a"])
    # Default forwards no explicit strategy (engine resolves None -> fail-closed
    # "wait_all"); crucially it is NOT the ignore_failures opt-out.
    assert captured["failure_strategy"] is None
    assert "fail_fast" not in captured


def test_flow_invoke_parallel_has_no_fail_fast_parameter():
    """``failure_strategy`` is the one switch; the ``fail_fast`` bool is gone."""
    import inspect

    assert "fail_fast" not in inspect.signature(flow.invoke_parallel).parameters


# ---------------------------------------------------------------------------
# cost.record / cost.model_pricing
# ---------------------------------------------------------------------------


async def test_cost_record_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await cost.record("x", 0.0)


async def test_cost_record_delegates(stub_ext):
    seen = []

    async def record_cost(label, amount, currency, metadata):
        seen.append((label, amount, currency, metadata))

    stub_ext.record_cost = record_cost
    await cost.record("api", 0.05, currency="EUR", metadata={"p": "x"})
    assert seen == [("api", 0.05, "EUR", {"p": "x"})]


async def test_cost_model_pricing_delegates(stub_ext):
    async def get_llm_pricing(provider, model):
        # `model_name`, not `model`. A stub with the key wrong would pass
        # the dict compare below unnoticed.
        return {
            "provider": provider,
            "model_name": model,
            "input_price_per_1m_usd": 3.0,
            "output_price_per_1m_usd": 15.0,
            "our_cost_input_per_1m_usd": 3.0,
            "our_cost_output_per_1m_usd": 15.0,
        }

    stub_ext.get_llm_pricing = get_llm_pricing
    out = await cost.model_pricing("anthropic", "claude-sonnet-4-5")
    assert out.model_name == "claude-sonnet-4-5"
    assert out.input_price_per_1m_usd == 3.0
