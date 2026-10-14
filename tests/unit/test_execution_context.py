"""T2: _ExecutionContext stack semantics."""

from __future__ import annotations

import pytest

from perceivo._internal.context import _ExecutionContext


def test_get_ext_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError, match="not available outside component execution"):
        _ExecutionContext.get_ext()


def test_set_and_get_ext_roundtrip(cleared_context):
    ext = object()
    _ExecutionContext.set_ext(ext)
    try:
        assert _ExecutionContext.get_ext() is ext
        assert _ExecutionContext.is_active() is True
        assert _ExecutionContext.depth() == 1
    finally:
        _ExecutionContext.clear()


def test_stack_pop_restores_previous(cleared_context):
    a = object()
    b = object()
    _ExecutionContext.set_ext(a)
    _ExecutionContext.set_ext(b)
    try:
        assert _ExecutionContext.get_ext() is b
        assert _ExecutionContext.depth() == 2
        _ExecutionContext.clear()
        assert _ExecutionContext.get_ext() is a
    finally:
        _ExecutionContext.clear_all()


def test_clear_all_empties_stack(cleared_context):
    for _ in range(3):
        _ExecutionContext.set_ext(object())
    _ExecutionContext.clear_all()
    assert _ExecutionContext.is_active() is False
    assert _ExecutionContext.depth() == 0


def test_clear_on_empty_stack_is_safe(cleared_context):
    # Should not raise even when nothing to pop.
    _ExecutionContext.clear()
    assert _ExecutionContext.is_active() is False
