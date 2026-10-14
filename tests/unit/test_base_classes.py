"""T1: Component base-class abstract-method enforcement."""

from __future__ import annotations

import asyncio

import pytest

from perceivo.primitives._base import FSM, Adapter, Agent, End, Policy, Start, Tool

ABSTRACT_PAIRS = [
    (Start, "start"),
    (Agent, "run"),
    (End, "end"),
    (Tool, "run"),
    (FSM, "run"),
    (Policy, "run"),
    (Adapter, "run"),
]


@pytest.mark.parametrize("base,_method", ABSTRACT_PAIRS)
def test_subclass_without_abstract_method_cannot_instantiate(base, _method):
    class MissingImpl(base):
        pass

    with pytest.raises(TypeError):
        MissingImpl()


@pytest.mark.parametrize("base,method_name", ABSTRACT_PAIRS)
def test_subclass_with_abstract_method_instantiates(base, method_name):
    namespace: dict = {}

    async def _impl(self, input):
        return "ok"

    namespace[method_name] = _impl
    cls = type("Concrete", (base,), namespace)
    instance = cls()
    assert asyncio.run(getattr(instance, method_name)(None)) == "ok"


def test_internal_underscore_methods_delegate_to_public():
    class MyAgent(Agent):
        async def run(self, input):
            return "agent-ran"

    assert asyncio.run(MyAgent()._run(None)) == "agent-ran"

    class MyStart(Start):
        async def start(self, input):
            return "start-ran"

    assert asyncio.run(MyStart()._start(None)) == "start-ran"

    class MyEnd(End):
        async def end(self, input):
            return "end-ran"

    assert asyncio.run(MyEnd()._end(None)) == "end-ran"


def test_default_setup_is_noop_on_each_base():
    for base, method_name in ABSTRACT_PAIRS:

        async def _impl(self, input):
            return None

        cls = type("Concrete", (base,), {method_name: _impl})
        assert asyncio.run(cls().setup()) is None
