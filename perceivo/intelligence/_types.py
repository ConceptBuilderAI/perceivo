"""Define result types for the ``intelligence`` planes.

Timestamps are ``str``: each arrives as ISO-8601 text already serialized by
the producer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["FeatureView", "Materialization", "PredictionRecord"]


@dataclass(frozen=True)
class FeatureView:
    """One feature view in the offline store, with its latest version."""

    #: The view name, as the ingest task wrote it.
    name: str
    #: Latest version in the offline store, starting at 1. A view with no data
    #: does not appear in the mapping.
    version: int


@dataclass(frozen=True)
class Materialization:
    """The outcome of copying offline features into the online store."""

    #: ``"completed"``. A failure raises rather than reporting a status.
    status: str
    #: The views actually processed, ``[]`` when none were requested.
    feature_views: list[str]
    #: Keys written across every view. A view whose parquet is missing is logged
    #: and skipped, so a partial result reports a real count rather than raising.
    keys_written: int
    #: Present only when no views were requested, explaining that only
    #: explicitly named views are materialized.
    message: str | None = None


@dataclass(frozen=True)
class PredictionRecord:
    """One logged prediction, as returned by ``inference.predictions()``.

    Distinct from :class:`~perceivo.types.records.Prediction`, which is the
    model's live response from ``inference.predict()``; this is a log line read
    back afterwards. They share no field.

    Every key is always present, but a writer that omitted a field leaves
    ``None``. Only ``intelligence_id`` and ``is_error`` are guaranteed
    non-``None``.
    """

    #: ``str(intelligence_id)``, never ``None``.
    intelligence_id: str
    #: Defaults to ``False``, never ``None``.
    is_error: bool
    #: The log line's ``id``.
    prediction_id: str | None
    #: Which model served it.
    model_id: str | None
    #: The model family, when the writer resolved one.
    family_id: str | None
    #: Subject of the prediction: the user or item id.
    entity_id: str | None
    #: The caller's correlation id, falling back to ``request_id``.
    correlation_id: str | None
    #: The platform request id.
    request_id: str | None
    #: The service that produced the prediction.
    served_by: str | None
    #: ``logged_at`` when present, else ``timestamp``.
    timestamp: str | None
    #: End-to-end latency the writer recorded.
    latency_ms: int | None
    #: The features sent to the model, in the model's own shape.
    input_data: Any
    #: The model's output, in the model's own shape.
    prediction_result: Any
    #: Set when ``is_error`` is true.
    error_message: str | None
