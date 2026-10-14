"""T1: Component decorator tests."""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from perceivo.primitives import _decorators as decorators
from perceivo.primitives._base import FSM, Adapter, Agent, End, Policy, Review, Start, Tool

DECORATOR_BASES = [
    ("agent", Agent),
    ("tool", Tool),
    ("fsm", FSM),
    ("policy", Policy),
    ("start", Start),
    ("end", End),
    ("adapter", Adapter),
    ("review", Review),
]


def _exec_in_fresh_module(source: str) -> dict:
    """Execute decorator-using source in a clean module-globals dict.

    Required because each decorator injects ``ENTRY_CLASS`` into the
    caller's globals and refuses a second decorator in the same module.
    """
    ns: dict = {"__name__": "tests._scratch_decorator_module"}
    exec(compile(textwrap.dedent(source), "<test>", "exec"), ns)
    return ns


@pytest.mark.parametrize("name,base", DECORATOR_BASES)
def test_decorator_injects_entry_class_and_inherits(name, base):
    ns = _exec_in_fresh_module(f"""
        import perceivo
        @perceivo.{name}
        async def my_component(input):
            return None
    """)
    assert ns["ENTRY_CLASS"] == "my_component"
    cls = ns["my_component"]
    assert issubclass(cls, base)


def test_there_is_no_layer_decorator():
    assert not hasattr(decorators, "layer")


@pytest.mark.parametrize("name,_base", DECORATOR_BASES)
def test_decorator_rejects_sync_function(name, _base):
    with pytest.raises(TypeError, match="async"):
        _exec_in_fresh_module(f"""
            import perceivo
            @perceivo.{name}
            def my_component(input):
                return None
        """)


def test_duplicate_decorator_in_same_module_raises():
    with pytest.raises(RuntimeError, match="Only one perceivo component decorator"):
        _exec_in_fresh_module("""
            import perceivo
            @perceivo.agent
            async def first(input):
                return None
            @perceivo.tool
            async def second(input):
                return None
        """)


def test_decorated_callable_runs_user_function():
    ns = _exec_in_fresh_module("""
        import perceivo
        @perceivo.tool
        async def doubler(input):
            return {"doubled": input["x"] * 2}
    """)
    cls = ns["doubler"]
    instance = cls()
    result = asyncio.run(instance.run({"x": 21}))
    assert result == {"doubled": 42}


def test_setup_decorator_registers_async_warmup():
    ns = _exec_in_fresh_module("""
        import perceivo

        warmup_calls = []

        @perceivo.agent
        async def my_agent(input):
            return None

        @my_agent.setup
        async def warmup():
            warmup_calls.append("ran")
            return "ready"
    """)
    cls = ns["my_agent"]
    assert asyncio.run(cls().setup()) == "ready"
    assert ns["warmup_calls"] == ["ran"]


def test_setup_decorator_rejects_sync_function():
    with pytest.raises(TypeError, match="async"):
        _exec_in_fresh_module("""
            import perceivo
            @perceivo.agent
            async def my_agent(input):
                return None
            @my_agent.setup
            def warmup():
                pass
        """)


@pytest.mark.parametrize(
    "name,_base",
    [
        ("start", Start),
        ("end", End),
    ],
)
def test_start_end_method_name_matches_decorator(name, _base):
    ns = _exec_in_fresh_module(f"""
        import perceivo
        @perceivo.{name}
        async def boundary(input):
            return "ok"
    """)
    cls = ns["boundary"]
    method = getattr(cls, name)
    assert asyncio.iscoroutinefunction(method)
