"""``timeout_seconds`` is a deadline on the local parallel path, not decoration.

``perceivo.flow.invoke_parallel`` documents ``timeout_seconds`` as *"Maximum
time to wait for all responses"* and the whole chain forwards it
(``flow/invocation.py`` -> ``local/context.py`` -> ``runner/_ports.py``). Until
this module existed the runner bound the value and never read it: the only
awaiting construct was ``asyncio.gather(..., return_exceptions=True)`` with no
deadline, so a branch that never returns hung a local run with **no exit code
at all**, while the same orchestration is bounded in-cluster.

Every case here drives the real ``_ports.invoke_port_parallel`` with only the
single-port call stubbed, and every one carries its own outer guard so a
regression fails the suite instead of hanging it. The guard's ``TimeoutError``
is empty; the runner's says ``Timeout after 1s`` -- the two are never confused,
which is what makes these tests discriminating rather than merely green.

Parity: the platform applies one deadline to the whole call, cancels the branches
still running at it, records each with the text ``f"Timeout after {timeout_seconds}s"``,
and raises under a fail-closed strategy once a branch is marked failed.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from perceivo.runner import _ports

# The declared deadline under test, and the outer guard that turns a hang into
# a failure. The guard is 20x the deadline: nothing that honours the deadline
# can reach it, and nothing that ignores the deadline can avoid it.
_DEADLINE = 1
_GUARD = 20.0
# A run that honours the deadline finishes at ~1s. 10s leaves an order of
# magnitude of headroom for a loaded box while staying far below the guard.
_BOUND = 10.0
# How long a cancelled branch spends in its own cleanup before it is gone.
# Non-zero on purpose: a caller that cancels without awaiting returns while
# this is still running, and that is the property these tests pin.
_CLEANUP = 0.05
# A deadline the cancellation case never reaches, so that what ends the call
# is the caller's cancellation and nothing else.
_UNREACHED = 300


class _FakeExecutor:
    """Records which ports hang and which branch coroutines saw cancellation."""

    def __init__(
        self,
        hanging_ports: set[str],
        self_cancelling_ports=frozenset(),
        failing_ports=frozenset(),
    ):
        self.hanging_ports = set(hanging_ports)
        self.self_cancelling_ports = set(self_cancelling_ports)
        self.failing_ports = set(failing_ports)
        self.cancelled: list[str] = []
        self.entered: list[str] = []
        # The run's artifacts, read only for the main artifact to fall back to.
        self._artifacts: dict = {}


async def _fake_invoke_target(
    executor, *, port_name, request_data=None, input_artifact=None, input_artifacts=None
):
    executor.entered.append(port_name)
    if port_name in executor.self_cancelling_ports:
        # A branch that raises CancelledError of its own accord -- an inner
        # wait_for firing, a client cancelling its own work. CPython marks the
        # task cancelled for this, which is what makes ``task.exception()``
        # unsafe to read without a guard.
        raise asyncio.CancelledError
    if port_name in executor.failing_ports:
        # A branch that fails immediately -- no cancellation involved. It pins
        # that every branch's exception is retrieved before fail_fast (or an
        # outer cancellation) raises past the others.
        raise ValueError(f"boom-{port_name}")
    if port_name not in executor.hanging_ports:
        return {"artifact": None, "response_data": {"port": port_name}}
    try:
        # Never returns on its own. Only cancellation ends this branch.
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        # Await BEFORE recording, so this records only if the caller awaited
        # the cancellation it requested. ``task.cancel()`` alone leaves this
        # handler suspended here and ``executor.cancelled`` empty: that is the
        # difference between a branch that has ended and one that has merely
        # been asked to.
        await asyncio.sleep(_CLEANUP)
        executor.cancelled.append(port_name)
        raise
    raise AssertionError("unreachable")  # pragma: no cover - defensive


@pytest.fixture
def invoke_parallel(monkeypatch):
    """Run the REAL ``invoke_port_parallel`` with single-port invoke stubbed."""
    monkeypatch.setattr(_ports, "invoke_target", _fake_invoke_target)

    async def run(
        hanging_ports, self_cancelling_ports=frozenset(), failing_ports=frozenset(), **kwargs
    ):
        executor = _FakeExecutor(hanging_ports, self_cancelling_ports, failing_ports)
        started = time.monotonic()
        try:
            outcome = await asyncio.wait_for(
                _ports.invoke_port_parallel(executor, **kwargs), timeout=_GUARD
            )
        except BaseException as exc:  # the outcome under test, whichever it is
            return executor, time.monotonic() - started, exc
        return executor, time.monotonic() - started, outcome

    return run


async def test_wait_all_raises_the_engines_timeout_text_within_the_deadline(invoke_parallel):
    """wait_all is fail-closed, so a timed-out branch fails the call."""
    executor, elapsed, outcome = await invoke_parallel(
        {"slow"},
        port_names=["fast", "slow"],
        timeout_seconds=_DEADLINE,
        failure_strategy="wait_all",
    )
    assert isinstance(outcome, TimeoutError)
    # The engine's per-branch text, not the bare guard.
    assert str(outcome) == f"Timeout after {_DEADLINE}s"
    assert elapsed < _BOUND
    assert executor.cancelled == ["slow"]


async def test_fail_fast_raises_the_engines_timeout_text_within_the_deadline(invoke_parallel):
    """fail_fast is fail-closed too: a timeout raises rather than returning."""
    executor, elapsed, outcome = await invoke_parallel(
        {"slow"},
        port_names=["fast", "slow"],
        timeout_seconds=_DEADLINE,
        failure_strategy="fail_fast",
    )
    assert isinstance(outcome, TimeoutError)
    assert str(outcome) == f"Timeout after {_DEADLINE}s"
    assert elapsed < _BOUND
    assert executor.cancelled == ["slow"]


async def test_ignore_failures_returns_the_timeout_as_a_branch_error(invoke_parallel):
    """ignore_failures is the one opt-out: the timeout arrives as ``error``."""
    executor, elapsed, outcome = await invoke_parallel(
        {"slow"},
        port_names=["fast", "slow"],
        timeout_seconds=_DEADLINE,
        failure_strategy="ignore_failures",
    )
    assert isinstance(outcome, dict), outcome
    assert elapsed < _BOUND
    # The branch that finished keeps its payload; the timed-out one carries the
    # engine's text in the same ``error`` slot any other branch failure uses.
    assert outcome["fast"] == {"artifact": None, "response_data": {"port": "fast"}}
    assert outcome["slow"] == {
        "artifact": None,
        "response_data": {},
        "error": f"Timeout after {_DEADLINE}s",
    }
    assert executor.cancelled == ["slow"]


async def test_the_legacy_fail_fast_bool_also_bounds_the_wait(invoke_parallel):
    """The deadline does not depend on the strategy being passed explicitly."""
    _, elapsed, outcome = await invoke_parallel(
        {"slow"},
        port_names=["slow"],
        timeout_seconds=_DEADLINE,
        fail_fast=True,
    )
    assert isinstance(outcome, TimeoutError)
    assert str(outcome) == f"Timeout after {_DEADLINE}s"
    assert elapsed < _BOUND


async def test_the_default_strategy_bounds_the_wait(invoke_parallel):
    """No strategy and no bool still resolves to wait_all, and still bounds."""
    _, elapsed, outcome = await invoke_parallel(
        {"slow"},
        port_names=["slow"],
        timeout_seconds=_DEADLINE,
    )
    assert isinstance(outcome, TimeoutError)
    assert str(outcome) == f"Timeout after {_DEADLINE}s"
    assert elapsed < _BOUND


async def test_pending_branch_tasks_are_cancelled_not_leaked(invoke_parallel):
    """A timeout must end the branches, not abandon them.

    Two independent instruments, because "it raised" says nothing about what is
    still running: the branch coroutine records its own cancellation, and the
    event loop's task census must return to what it was before the call.
    """
    before = {t for t in asyncio.all_tasks() if not t.done()}
    executor, _, outcome = await invoke_parallel(
        {"slowA", "slowB"},
        port_names=["fast", "slowA", "slowB"],
        timeout_seconds=_DEADLINE,
        failure_strategy="ignore_failures",
    )
    assert isinstance(outcome, dict), outcome
    assert sorted(executor.cancelled) == ["slowA", "slowB"]
    leaked = {t for t in asyncio.all_tasks() if not t.done()} - before
    assert leaked == set(), [t.get_coro() for t in leaked]


async def test_branches_inside_the_deadline_are_untouched(invoke_parallel):
    """The deadline is a bound, not a delay: fast branches return immediately."""
    executor, elapsed, outcome = await invoke_parallel(
        set(),
        port_names=["a", "b"],
        timeout_seconds=_DEADLINE,
        failure_strategy="wait_all",
    )
    assert outcome == {
        "a": {"artifact": None, "response_data": {"port": "a"}},
        "b": {"artifact": None, "response_data": {"port": "b"}},
    }
    # Dict equality ignores order, so it does not pin "built in port_names
    # order" -- iterating reversed(port_names) would pass it. list(outcome)
    # reads insertion order and does pin it.
    assert list(outcome) == ["a", "b"]
    assert elapsed < _DEADLINE
    assert executor.cancelled == []


async def test_a_falsy_timeout_means_no_deadline(invoke_parallel):
    """``timeout_seconds=0`` is the engine's ``timeout=None``: wait for them all."""
    _, _, outcome = await invoke_parallel(
        set(),
        port_names=["a"],
        timeout_seconds=0,
        failure_strategy="wait_all",
    )
    assert outcome == {"a": {"artifact": None, "response_data": {"port": "a"}}}


async def test_no_ports_is_still_an_empty_result(invoke_parallel):
    """An empty port list returns ``{}`` rather than tripping ``asyncio.wait``."""
    _, _, outcome = await invoke_parallel(
        set(),
        port_names=[],
        timeout_seconds=_DEADLINE,
        failure_strategy="wait_all",
    )
    assert outcome == {}


async def test_cancelling_the_caller_cancels_every_branch(monkeypatch):
    """Cancellation must reach the branches, not stop at the call.

    ``asyncio.gather`` propagates the awaiting task's cancellation to its
    children; ``asyncio.wait`` does not. Applying the deadline with ``wait``
    buys the bound at the cost of that propagation unless it is put back by
    hand, and every other case in this module is blind to the loss: they all
    cancel from the inside, on the deadline. This is the only one that cancels
    from the outside -- an outer ``wait_for``, a component's own timeout, a
    loop shutting down -- which is how a real caller ends a call early.
    """
    monkeypatch.setattr(_ports, "invoke_target", _fake_invoke_target)
    executor = _FakeExecutor({"slow"})
    before = {t for t in asyncio.all_tasks() if not t.done()}
    call = asyncio.create_task(
        _ports.invoke_port_parallel(
            executor,
            port_names=["slow"],
            timeout_seconds=_UNREACHED,
            failure_strategy="wait_all",
        )
    )
    # Cancel only once the branch is actually running: cancelling a call whose
    # branches have not started proves nothing about branches.
    while not executor.entered:
        await asyncio.sleep(0)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    # Two instruments again, because "it raised" says nothing about what is
    # still running: the branch records its own cancellation, and the loop's
    # task census must come back to where it started.
    assert executor.cancelled == ["slow"]
    leaked = {t for t in asyncio.all_tasks() if not t.done()} - before
    assert leaked == set(), [t.get_coro() for t in leaked]


async def test_a_self_cancelling_branch_is_recorded_under_ignore_failures(invoke_parallel):
    """A branch that raises ``CancelledError`` is a branch failure, not an abort.

    ``Task.exception()`` RAISES ``CancelledError`` on a cancelled task rather
    than returning it, so reading it unguarded ends the whole parallel call --
    including under ``ignore_failures``, whose entire contract is that it does
    not. Parity: the platform reads ``task.exception()`` only behind
    ``if not task.cancelled()``.
    """
    _, _, outcome = await invoke_parallel(
        set(),
        self_cancelling_ports={"gone"},
        port_names=["fast", "gone"],
        timeout_seconds=_DEADLINE,
        failure_strategy="ignore_failures",
    )
    assert isinstance(outcome, dict), outcome
    assert outcome["fast"] == {"artifact": None, "response_data": {"port": "fast"}}
    assert outcome["gone"] == {
        "artifact": None,
        "response_data": {},
        "error": "Branch cancelled",
    }


async def test_a_self_cancelling_branch_fails_the_call_under_wait_all(invoke_parallel):
    """Fail-closed still means fail-closed -- but as a branch error.

    Not as a raw ``CancelledError`` escaping, which an awaiting caller reads as
    *its own* cancellation rather than as the call having failed.
    """
    _, _, outcome = await invoke_parallel(
        set(),
        self_cancelling_ports={"gone"},
        port_names=["gone"],
        timeout_seconds=_DEADLINE,
        failure_strategy="wait_all",
    )
    assert isinstance(outcome, RuntimeError), outcome
    assert not isinstance(outcome, asyncio.CancelledError)
    assert str(outcome) == "Branch cancelled"


# --- a duplicated port name must not run its target twice or leak ---------


async def test_a_duplicated_port_name_enters_its_target_once_and_leaks_nothing(invoke_parallel):
    """One task per occurrence, tracked by name, would keep only the last one
    (dict overwrite), so the target would run twice and the first task would
    never be awaited, cancelled or drained past the call's return.
    """
    before = {t for t in asyncio.all_tasks() if not t.done()}
    executor, _, outcome = await invoke_parallel(
        {"a"},
        port_names=["a", "a"],
        timeout_seconds=_DEADLINE,
        failure_strategy="ignore_failures",
    )
    assert isinstance(outcome, dict), outcome
    assert executor.entered == ["a"]
    assert executor.cancelled == ["a"]
    assert outcome == {
        "a": {
            "artifact": None,
            "response_data": {},
            "error": f"Timeout after {_DEADLINE}s",
        }
    }
    leaked = {t for t in asyncio.all_tasks() if not t.done()} - before
    assert leaked == set(), [t.get_coro() for t in leaked]


# --- every branch's exception must be retrieved, even the ones a ----------
# --- fail-closed strategy or an outer cancellation never returns to a caller


async def test_fail_fast_retrieves_every_branch_exception_before_raising(invoke_parallel):
    """fail_fast reads every branch before raising on the first failure.

    An unread task makes asyncio log "Task exception was never retrieved" once
    it is garbage collected -- noise on a call whose reported outcome is
    correct. The raised error is the first branch's (order is not incidental).
    """
    loop = asyncio.get_running_loop()
    unretrieved: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
    try:
        _, _, outcome = await invoke_parallel(
            set(),
            failing_ports={"b1", "b2"},
            port_names=["b1", "b2"],
            timeout_seconds=_DEADLINE,
            failure_strategy="fail_fast",
        )
        assert isinstance(outcome, ValueError), outcome
        assert str(outcome) == "boom-b1"
        # Force GC now, inside the test, rather than trusting a later
        # collection to happen before the handler is restored.
        import gc

        del outcome
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)
    assert unretrieved == [], unretrieved


async def test_cancel_and_drain_retrieves_an_already_done_tasks_exception():
    """The outer-cancel path passes _cancel_and_drain the WHOLE branch
    set, including tasks that had already failed before the cancellation
    arrived. Draining only the unfinished ones would leave an already-failed
    task's exception unread.
    """

    async def _fail() -> dict[str, Any]:
        raise ValueError("already-failed")

    async def _hang() -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover - defensive

    done_task: asyncio.Task[dict[str, Any]] = asyncio.ensure_future(_fail())
    pending_task: asyncio.Task[dict[str, Any]] = asyncio.ensure_future(_hang())
    # Let the failing task actually finish (exception set) before draining --
    # this is the "already done" precondition the finding describes.
    await asyncio.sleep(0)
    assert done_task.done()
    assert not pending_task.done()

    loop = asyncio.get_running_loop()
    unretrieved: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
    try:
        await _ports._cancel_and_drain({done_task, pending_task})
        del done_task, pending_task
        import gc

        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)
    assert unretrieved == [], unretrieved
