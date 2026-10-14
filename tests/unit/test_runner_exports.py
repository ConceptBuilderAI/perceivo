"""``perceivo.runner`` is four verbs, and the result types are not on it.

The plane's verbs are `history`, `run_component`, `run_orchestration` and
`run_stream`. A plane carries its verbs; every type is exported once, from a
types module, and never again from the plane that returns it. So
``from perceivo.runner import ExecutionResult`` fails; the type lives in
``perceivo.runner.types``.

The other side of the guarantee: the types are importable by name, at the
module that defines them, and the exported object and the defining module's object are the
same object.
"""

from __future__ import annotations

import perceivo.runner as runner

#: The eight types the plane does not re-export, and where each one lives.
MOVED = {
    "CompileResult": "perceivo.runner.types",
    "ExecutionResult": "perceivo.runner.types",
    "NodeShell": "perceivo.runner.types",
    "NodeTrace": "perceivo.runner.types",
    "PortConnection": "perceivo.runner.types",
    "EventType": "perceivo.runner.events",
    "ExecutionEvent": "perceivo.runner.events",
    "RunHistory": "perceivo.runner.history",
}


def test_the_plane_is_exactly_the_four_verbs_and_run_record():
    """``RunRecord`` is the one type here and it is not an exception to the rule:
    it is a plane-owned type, not one of this plane's four verbs."""
    assert sorted(runner.__all__) == [
        "RunRecord",
        "history",
        "run_component",
        "run_orchestration",
        "run_stream",
    ]


def test_the_result_types_are_off_the_plane():
    for name in MOVED:
        assert name not in runner.__all__, f"perceivo.runner.__all__ still lists {name}"
        assert not hasattr(runner, name), f"perceivo.runner.{name} is still reachable"
    # Positive control: a plane that failed to import, or that lost its verbs
    # too, would pass the loop above by being empty.
    assert callable(runner.run_component)
    assert callable(runner.run_orchestration)


def test_each_moved_type_is_importable_at_the_module_that_defines_it():
    """The removal is a namespace change, not a deletion."""
    import importlib

    for name, where in MOVED.items():
        module = importlib.import_module(where)
        assert hasattr(module, name), f"{where}.{name} does not exist"


def test_run_record_is_the_same_object_the_history_module_defines():
    from perceivo.runner.history import RunRecord as _FromHistory

    assert runner.RunRecord is _FromHistory
