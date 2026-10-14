"""Intelligence components and the intelligence read planes.

An *intelligence component* is the artifact a user writes; an *intelligence
service* is the pod that runs it. Each of the four stages has a base class::

    perceivo.intelligence.FeatureComponent      ingest features
    perceivo.intelligence.TrainingComponent     train a model
    perceivo.intelligence.InferenceComponent    serve predictions
    perceivo.intelligence.EvaluationComponent   decide what happens to a model

Two read planes reach a running intelligence from inside a run:

* ``intelligence.features``: feature-view reads. Online records are addressed
  by feature view and a set of entity ids,
  ``get_online_records(feature_view, entity_ids=[...])``.
* ``intelligence.inference``: ``predict`` against a deployed model, and
  ``predictions`` to read back past predictions.
"""

from __future__ import annotations

from . import features, inference
from ._components import (
    EvaluationComponent,
    FeatureComponent,
    InferenceComponent,
    TrainingComponent,
)

__all__ = [
    "EvaluationComponent",
    "FeatureComponent",
    "InferenceComponent",
    "TrainingComponent",
    "features",
    "inference",
]
