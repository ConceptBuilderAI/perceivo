"""IngestPlan.batch_size must be positive, so batch_count never divides by zero."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from perceivo.types.features import IngestPlan


def test_batch_size_zero_is_rejected():
    with pytest.raises(ValidationError):
        IngestPlan(total_items=10, batch_size=0)


def test_batch_size_negative_is_rejected():
    with pytest.raises(ValidationError):
        IngestPlan(total_items=10, batch_size=-1)


def test_batch_size_one_works():
    plan = IngestPlan(total_items=10, batch_size=1)
    assert plan.batch_size == 1
    assert plan.batch_count == 10


def test_batch_count_never_fewer_than_one():
    assert IngestPlan(total_items=0).batch_count == 1
