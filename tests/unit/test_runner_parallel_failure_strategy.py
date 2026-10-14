"""The local runner honors ``failure_strategy``.

The platform orchestration service's engine makes ``invoke_parallel`` fail-closed
by default (``wait_all``/``fail_fast`` raise on a branch failure) with an
explicit ``ignore_failures`` opt-out. These tests prove the SDK's local runner
(``perceivo.runner._ports.invoke_port_parallel``) mirrors that contract, so the
opt-out is exercisable without the capability image.
"""

from __future__ import annotations

import pytest

from perceivo.runner import _ports
from perceivo.runner._invoke_result import resolve_failure_strategy as _resolve_failure_strategy


class _FakeExecutor:
    """Minimal host carrying the per-branch outcomes and the run's artifacts.

    ``invoke_port_parallel`` does not reach through the executor to run a
    single port -- it calls the module-level :func:`_ports.invoke_target` -- so the
    stub is installed there and this object records which ports fail. It reads
    the run's artifacts only for the main artifact to fall back to.
    """

    def __init__(self, failing_ports: set[str]):
        self._failing_ports = set(failing_ports)
        self._artifacts: dict = {}


async def _fake_invoke_target(
    executor, *, port_name, request_data=None, input_artifact=None, input_artifacts=None
):
    if port_name in executor._failing_ports:
        raise RuntimeError(f"branch {port_name} boom")
    return {"artifact": None, "response_data": {"port": port_name}}


@pytest.fixture
def invoke_parallel(monkeypatch):
    """Run the REAL ``invoke_port_parallel`` with single-port invoke stubbed.

    Only the one call it delegates to is replaced, so the strategy handling under
    test is production code rather than a copy of it.
    """
    monkeypatch.setattr(_ports, "invoke_target", _fake_invoke_target)

    async def run(failing_ports, **kwargs):
        return await _ports.invoke_port_parallel(_FakeExecutor(failing_ports), **kwargs)

    return run


# --- resolver (mirrors the engine's _resolve_failure_strategy) --------------


def test_resolve_explicit_strategy_wins():
    assert _resolve_failure_strategy("ignore_failures") == "ignore_failures"
    assert _resolve_failure_strategy("fail_fast") == "fail_fast"
    assert _resolve_failure_strategy("wait_all") == "wait_all"
    assert _resolve_failure_strategy("ignore_failures") == "ignore_failures"


def test_resolve_normalizes_case_and_whitespace():
    assert _resolve_failure_strategy("  Ignore_Failures ") == "ignore_failures"


def test_resolve_absent_strategy_is_fail_closed():
    assert _resolve_failure_strategy(None) == "wait_all"
    assert _resolve_failure_strategy("") == "wait_all"


def test_resolve_unknown_falls_back_to_fail_closed():
    assert _resolve_failure_strategy("continue_anyway") == "wait_all"


# --- behavior: ignore_failures is the only strategy that swallows -----------


async def test_ignore_failures_swallows_and_records_error(invoke_parallel):
    results = await invoke_parallel(
        {"p_bad"}, port_names=["p_ok", "p_bad"], failure_strategy="ignore_failures"
    )
    assert set(results) == {"p_ok", "p_bad"}
    assert results["p_ok"]["response_data"] == {"port": "p_ok"}
    assert "boom" in results["p_bad"]["error"]


async def test_default_is_fail_closed_wait_all(invoke_parallel):
    with pytest.raises(RuntimeError, match="boom"):
        # No strategy -> wait_all (fail-closed).
        await invoke_parallel({"p_bad"}, port_names=["p_ok", "p_bad"])


async def test_explicit_wait_all_raises(invoke_parallel):
    with pytest.raises(RuntimeError, match="boom"):
        await invoke_parallel({"p_bad"}, port_names=["p_ok", "p_bad"], failure_strategy="wait_all")


async def test_fail_fast_raises(invoke_parallel):
    with pytest.raises(RuntimeError, match="boom"):
        await invoke_parallel({"p_bad"}, port_names=["p_bad", "p_ok"], failure_strategy="fail_fast")


async def test_all_success_returns_every_branch(invoke_parallel):
    results = await invoke_parallel(
        set(), port_names=["p1", "p2"], failure_strategy="ignore_failures"
    )
    assert results["p1"]["response_data"] == {"port": "p1"}
    assert results["p2"]["response_data"] == {"port": "p2"}
