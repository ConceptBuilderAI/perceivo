"""The root re-exports each primitive decorator as the *same object*.

The canonical home of
every primitive is ``perceivo.primitives``, and the root re-exports each
decorator -- ``perceivo.start``, ``perceivo.end``, ``perceivo.agent``,
``perceivo.tool``, ``perceivo.fsm``, ``perceivo.policy``, ``perceivo.adapter``
and ``perceivo.review``. Each is the same object as its ``primitives``
counterpart, is in the root ``__all__``, is not deprecated and carries no
warning. ``Layer``/``layer`` stay deleted, and no class is re-exported.
"""

from __future__ import annotations

import textwrap
import warnings

import pytest

import perceivo
import perceivo.primitives as primitives

DECORATORS = ("start", "end", "agent", "tool", "fsm", "policy", "adapter", "review")
CLASSES = ("Start", "End", "Agent", "Tool", "FSM", "Policy", "Adapter", "Review")


@pytest.mark.parametrize("name", DECORATORS)
def test_the_root_decorator_is_the_primitives_decorator(name):
    assert getattr(perceivo, name) is getattr(primitives, name)


@pytest.mark.parametrize("name", DECORATORS)
def test_the_root_decorator_is_declared_in_both_places(name):
    assert name in perceivo.__all__
    assert name in primitives.__all__


@pytest.mark.parametrize("name", CLASSES)
def test_the_classes_live_only_in_primitives(name):
    assert name in primitives.__all__
    assert not hasattr(perceivo, name), f"perceivo.{name} is a top-level class re-export"


@pytest.mark.parametrize("name", ["Layer", "layer", "LayerInput", "LayerOutput"])
def test_layer_stays_deleted(name):
    assert not hasattr(perceivo, name)
    assert not hasattr(primitives, name)


def test_using_a_root_decorator_warns_nothing():
    source = textwrap.dedent(
        """
        import perceivo

        @perceivo.review
        async def approve(input):
            return None
        """
    )
    namespace: dict = {"__name__": "tests._scratch_review_module"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        exec(compile(source, "<root-decorator>", "exec"), namespace)
    cls = namespace["approve"]
    assert issubclass(cls, primitives.Review)
    assert namespace["ENTRY_CLASS"] == "approve"
